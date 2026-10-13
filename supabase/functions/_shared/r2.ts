// Ky URL GET da ky truoc cho Cloudflare R2 (AWS SigV4).
//
// ⛔ BAN SONG DOI CUA `scripts/r2_sigv4.py`. SigV4 la mot chuoi bam long nhau;
// lech mot dau xuong dong trong canonical request la chu ky sai, va R2 tra ve
// 403 khong noi gi ve nguyen nhan - tuc loi chi lo ra khi khach bam cap nhat.
// `backend/tests/test_r2_sigv4_parity.py` so sanh TUNG BYTE ca canonical
// request lan URL cuoi voi ban Python.
//
// ⛔ VA `amzDate` NHAN TU NGOAI, khong goi `new Date()` ben trong. Mot ham ky
// tu lay gio hien tai thi khong the do bang nhau giua hai ban cai.
//
// ⛔ VI SAO R2 CHU KHONG PHAI Supabase Storage: Free plan cua Supabase chan
// upload o 50 MB va khong nang duoc tu dashboard. Backend 0.1.12 la 50,7 MiB -
// vuot dung 0,7 MiB. R2 cho 10 GB luu, khong tinh egress, va mot PUT toi 5 GB.
// `download_url` trong manifest tro vao function `artifacts` chu khong tro
// thang vao cho chua, nen doi sang R2 KHONG phai ky lai manifest nao.

const ALGORITHM = "AWS4-HMAC-SHA256";
const SERVICE = "s3";
/** R2 khong co region; S3 API cua no doi dung chuoi "auto". */
const REGION = "auto";
/** Presigned URL khong ky than request: client goi duoc ma khong co than. */
const UNSIGNED = "UNSIGNED-PAYLOAD";

/**
 * Escape theo RFC 3986: chi `-_.~` la khong escape.
 *
 * ⛔ `encodeURIComponent` CUA JS KHONG DU. No bo qua `!'()*` - bon ky tu do
 * hop le trong ten file va se lam chu ky lech voi ban Python (Python dung
 * `quote(safe="-_.~")`). Phai escape tay.
 */
function rfc3986(value: string, keepSlash = false): string {
  let out = "";
  for (const char of value) {
    if (/[A-Za-z0-9\-_.~]/.test(char) || (keepSlash && char === "/")) {
      out += char;
      continue;
    }
    for (const byte of new TextEncoder().encode(char)) {
      out += "%" + byte.toString(16).toUpperCase().padStart(2, "0");
    }
  }
  return out;
}

function toHex(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function hmacRaw(key: Uint8Array, message: string): Promise<Uint8Array> {
  const imported = await crypto.subtle.importKey(
    "raw",
    key as unknown as ArrayBuffer,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(
    await crypto.subtle.sign("HMAC", imported, new TextEncoder().encode(message)),
  );
}

async function sha256Hex(value: string): Promise<string> {
  return toHex(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)),
    ),
  );
}

async function signingKey(secret: string, datestamp: string): Promise<Uint8Array> {
  let key = new TextEncoder().encode(`AWS4${secret}`);
  for (const part of [datestamp, REGION, SERVICE, "aws4_request"]) {
    key = await hmacRaw(key, part);
  }
  return key;
}

export interface R2Config {
  accountId: string;
  bucket: string;
  accessKeyId: string;
  secretAccessKey: string;
}

/** Dung cho bai test parity: canonical request tho, truoc khi bam. */
export function canonicalGetRequest(
  config: R2Config,
  key: string,
  canonicalQuery: string,
): string {
  const host = `${config.accountId}.r2.cloudflarestorage.com`;
  const canonicalUri = `/${rfc3986(config.bucket, true)}/${rfc3986(key, true)}`;
  return [
    "GET",
    canonicalUri,
    canonicalQuery,
    `host:${host}\n`,
    "host",
    UNSIGNED,
  ].join("\n");
}

export function canonicalGetQuery(
  config: R2Config,
  options: { expiresSeconds: number; amzDate: string },
): string {
  const scope =
    `${options.amzDate.slice(0, 8)}/${REGION}/${SERVICE}/aws4_request`;
  const query: Record<string, string> = {
    "X-Amz-Algorithm": ALGORITHM,
    "X-Amz-Credential": `${config.accessKeyId}/${scope}`,
    "X-Amz-Date": options.amzDate,
    "X-Amz-Expires": String(options.expiresSeconds),
    "X-Amz-SignedHeaders": "host",
  };
  // Sort theo ten tham so, va escape ca ten lan gia tri.
  return Object.keys(query)
    .sort()
    .map((name) => `${rfc3986(name)}=${rfc3986(query[name])}`)
    .join("&");
}

export async function presignR2Get(
  config: R2Config,
  key: string,
  options: { expiresSeconds: number; amzDate: string },
): Promise<string> {
  if (
    options.amzDate.length !== 16 ||
    !options.amzDate.endsWith("Z") ||
    options.amzDate[8] !== "T"
  ) {
    throw new Error("amzDate phai dang YYYYMMDDTHHMMSSZ");
  }
  if (options.expiresSeconds < 1 || options.expiresSeconds > 604_800) {
    throw new Error("expiresSeconds phai trong [1, 604800]");
  }
  const datestamp = options.amzDate.slice(0, 8);
  const host = `${config.accountId}.r2.cloudflarestorage.com`;
  const canonicalUri = `/${rfc3986(config.bucket, true)}/${rfc3986(key, true)}`;
  const canonicalQuery = canonicalGetQuery(config, options);
  const canonical = canonicalGetRequest(config, key, canonicalQuery);
  const scope = `${datestamp}/${REGION}/${SERVICE}/aws4_request`;
  const toSign = [
    ALGORITHM,
    options.amzDate,
    scope,
    await sha256Hex(canonical),
  ].join("\n");
  const signature = toHex(
    await hmacRaw(await signingKey(config.secretAccessKey, datestamp), toSign),
  );
  return `https://${host}${canonicalUri}?${canonicalQuery}&X-Amz-Signature=${signature}`;
}

/** `YYYYMMDDTHHMMSSZ` tu mot moc thoi gian epoch (giay). */
export function amzDateFrom(epochSeconds: number): string {
  const iso = new Date(epochSeconds * 1000).toISOString();
  return iso.slice(0, 19).replace(/[-:]/g, "") + "Z";
}
