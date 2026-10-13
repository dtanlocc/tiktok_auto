// Ky lease Ed25519, dang token y het `backend/app/security/license.py`.
//
// ⛔ FILE NAY LA BAN THU HAI CUA MOT THU DA CO, VA DO LA RUI RO THAT SU CUA CA
// HUONG SUPABASE. Client xac thuc lease bang chu ky, nen mot byte lech trong
// JSON canonical la lease KHONG VERIFY DUOC - va loi do khong lo ra luc deploy,
// no lo ra luc khach bam Kich hoat. Vi vay:
//
//   1. `backend/tests/test_supabase_lease_parity.py` chay dung file nay bang
//      Node roi so SANH TUNG BYTE voi `LeaseSigner.sign()` cua Python. Lech la
//      test do, khong phai khach bao loi.
//   2. `canonicalJson` ben duoi KHONG phai JSON.stringify. No tu dung chuoi
//      theo thu tu khoa da sort va TU CHOI moi thu nam ngoai bon kieu claims
//      thuc su dung. Hep nhu vay la co y: khong the lech am tham o mot kieu du
//      lieu khong ai nghi den.
//
// Chay duoc tren ca Deno (Supabase Edge Runtime) va Node >= 22 vi chi dung
// WebCrypto + TextEncoder - khong import gi.

const LEASE_TYPE = "TKAUTO-LEASE";
const LEASE_ALGORITHM = "EdDSA";

export interface LeaseClaims {
  protocol_version: 1;
  license_id: string;
  device_id: string;
  plan: string;
  features: string[];
  max_accounts: number;
  max_tabs: number;
  channel: string;
  minimum_version: string;
  minimum_backend_version: string;
  issued_at: number;
  not_before: number;
  expires_at: number;
  jti: string;
}

export function b64urlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlDecode(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) {
    throw new Error("invalid base64url");
  }
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") +
    "=".repeat((4 - (value.length % 4)) % 4);
  const binary = atob(padded);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
  return out;
}

function assertAscii(value: string, label: string): void {
  // ⛔ DAY LA CHO THAY THE CHO `ensure_ascii=True`. Python escape moi ky tu
  // >= U+0080 thanh \uXXXX; JSON.stringify cua JS thi KHONG. Thay vi viet lai
  // thuat toan escape do va hy vong dung, chan hang khong-ASCII tu dau: moi
  // truong trong claims deu la identifier/semver/feature theo regex cua Python
  // nen vong doi that khong bao gio co ky tu nao >= 0x80. Chan o day bien mot
  // kha nang lech am tham thanh mot loi on ao.
  for (let i = 0; i < value.length; i += 1) {
    if (value.charCodeAt(i) > 0x7f) {
      throw new Error(`${label} chua ky tu khong phai ASCII`);
    }
  }
}

function jsonString(value: string, label: string): string {
  assertAscii(value, label);
  let out = '"';
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    const char = value[i];
    if (char === '"') out += '\\"';
    else if (char === "\\") out += "\\\\";
    else if (char === "\n") out += "\\n";
    else if (char === "\r") out += "\\r";
    else if (char === "\t") out += "\\t";
    else if (char === "\b") out += "\\b";
    else if (char === "\f") out += "\\f";
    else if (code < 0x20) out += "\\u" + code.toString(16).padStart(4, "0");
    else out += char;
  }
  return out + '"';
}

/**
 * JSON canonical giong `_canonical_json`: khoa da sort, phan cach "," va ":",
 * chi ASCII.
 *
 * ⛔ CHI NHAN string | integer | string[]. Claims that khong co kieu nao khac
 * (xem LeaseClaims trong license.py: khong boolean, khong null, khong so thuc).
 * Mot kieu la vao day se nem loi thay vi duoc serialize theo mot cach ma
 * Python khong lam giong.
 */
export function canonicalJson(value: Record<string, unknown>): Uint8Array {
  const keys = Object.keys(value).sort();
  const parts: string[] = [];
  for (const key of keys) {
    assertAscii(key, `khoa ${key}`);
    const item = value[key];
    let encoded: string;
    if (typeof item === "string") {
      encoded = jsonString(item, key);
    } else if (typeof item === "number") {
      if (!Number.isSafeInteger(item)) {
        throw new Error(`${key} phai la so nguyen an toan, nhan duoc ${item}`);
      }
      encoded = String(item);
    } else if (Array.isArray(item)) {
      const items = item.map((entry, index) => {
        if (typeof entry !== "string") {
          throw new Error(`${key}[${index}] phai la chuoi`);
        }
        return jsonString(entry, `${key}[${index}]`);
      });
      encoded = `[${items.join(",")}]`;
    } else {
      throw new Error(`${key} co kieu khong duoc ho tro: ${typeof item}`);
    }
    parts.push(`${jsonString(key, "khoa")}:${encoded}`);
  }
  return new TextEncoder().encode(`{${parts.join(",")}}`);
}

/**
 * Nap khoa rieng Ed25519 tu PEM PKCS8.
 *
 * ⛔ PEM NAY LA SECRET CUA EDGE FUNCTION, khong phai mot file trong repo. Khoa
 * RELEASE (ky manifest ban .exe) KHONG duoc dua len day: no duoc giu rieng vi
 * chiem duoc no la gui duoc ban cai doc cho khach, con chiem khoa lease chi la
 * dung lau app mien phi.
 */
export async function importLeasePrivateKey(pem: string): Promise<CryptoKey> {
  const body = pem
    .replace(/-----BEGIN [A-Z ]+-----/g, "")
    .replace(/-----END [A-Z ]+-----/g, "")
    .replace(/\s+/g, "");
  if (!body) throw new Error("PEM khoa lease rong");
  const der = Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
  return await crypto.subtle.importKey("pkcs8", der, "Ed25519", false, ["sign"]);
}

export async function signLease(
  key: CryptoKey,
  keyId: string,
  claims: LeaseClaims,
): Promise<string> {
  const header = {
    alg: LEASE_ALGORITHM,
    kid: keyId,
    typ: LEASE_TYPE,
    v: 1,
  };
  const encodedHeader = b64urlEncode(canonicalJson(header));
  const encodedClaims = b64urlEncode(
    canonicalJson(claims as unknown as Record<string, unknown>),
  );
  const signingInput = new TextEncoder().encode(
    `${encodedHeader}.${encodedClaims}`,
  );
  const signature = await crypto.subtle.sign("Ed25519", key, signingInput);
  return `${encodedHeader}.${encodedClaims}.${
    b64urlEncode(new Uint8Array(signature))
  }`;
}

// =============================================================================
// semver, giong `_parse_semver` / `_compare_semver`
// =============================================================================
const SEMVER_RE =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

type PreItem = [0, number] | [1, string];

function parseSemver(
  value: string,
): [number, number, number, PreItem[] | null] {
  const match = SEMVER_RE.exec(value);
  if (!match) throw new Error("version khong phai semver x.y.z");
  let pre: PreItem[] | null = null;
  if (match[4] !== undefined) {
    pre = [];
    for (const item of match[4].split(".")) {
      if (/^\d+$/.test(item)) {
        if (item.length > 1 && item.startsWith("0")) {
          throw new Error("prerelease so khong duoc co so 0 dan dau");
        }
        pre.push([0, Number(item)]);
      } else {
        pre.push([1, item]);
      }
    }
  }
  return [Number(match[1]), Number(match[2]), Number(match[3]), pre];
}

function compareItem(left: PreItem, right: PreItem): number {
  if (left[0] !== right[0]) return left[0] < right[0] ? -1 : 1;
  if (left[1] === right[1]) return 0;
  return left[1] < right[1] ? -1 : 1;
}

export function compareSemver(left: string, right: string): number {
  const a = parseSemver(left);
  const b = parseSemver(right);
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return (a[i] as number) < (b[i] as number) ? -1 : 1;
  }
  const aPre = a[3];
  const bPre = b[3];
  if (aPre === null && bPre === null) return 0;
  if (aPre === null) return 1;
  if (bPre === null) return -1;
  const shared = Math.min(aPre.length, bPre.length);
  for (let i = 0; i < shared; i += 1) {
    const verdict = compareItem(aPre[i], bPre[i]);
    if (verdict !== 0) return verdict;
  }
  if (aPre.length === bPre.length) return 0;
  return aPre.length < bPre.length ? -1 : 1;
}
