// Bang chung thiet bi + digest key, giong `control_plane/app/security.py`.

import { b64urlDecode } from "./lease.ts";

function toHex(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * HMAC-SHA256(pepper, key da chuan hoa) -> hex. Giong `digest_license_key`.
 *
 * ⛔ TU CHOI KEY KHONG-ASCII TRUOC KHI CHUAN HOA. Python lam
 * `"".join(key.upper().split())`, va `.upper()` cua Python voi `.toUpperCase()`
 * cua JS khong giong nhau tren vai ky tu Unicode (vi du U+0131). Mot key co ky
 * tu nhu vay se ra hai digest khac nhau o hai ban, tuc mot key "hop le o may
 * nay, sai o may kia". Key that la base32 + dau gach nen khong mat gi khi chan.
 */
export async function digestLicenseKey(
  licenseKey: string,
  pepper: string,
): Promise<string | null> {
  // ⛔ CHO PHEP KHOANG TRANG ASCII, chi chan tu 0x80 tro len. Ban dau cho nay
  // la `[\x20-\x7e]` va no tu choi luon key co tab hay dong moi - trong khi
  // Python `.split()` BO chung di roi van bam duoc binh thuong, tuc mot key
  // khach copy-paste lan dau se hop le o ban Python va sai o day.
  if (!/^[\x09-\x0d\x20-\x7e]+$/.test(licenseKey)) return null;
  // ⛔ LOP KY TU TUONG MINH, KHONG DUNG `\s`. `\s` cua JS bo luon U+00A0,
  // U+FEFF va cac dau cach Unicode khac ma Python `.split()` khong coi la
  // khoang trang; nguoc lai Python BO \x1c-\x1f ma `\s` khong. Hai khac biet
  // do deu dan den hai digest cho cung mot key. Lop duoi day la dung phan
  // giao: nhung gi vua qua duoc bo loc tren, vua bi Python `.split()` bo.
  const normalized = licenseKey.toUpperCase().replace(/[\x09-\x0d\x20]+/g, "");
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(pepper),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(normalized),
  );
  return toHex(new Uint8Array(mac));
}

/** Giong `canonical_device_proof`. */
export function canonicalDeviceProof(
  action: string,
  deviceId: string,
  issuedAt: number,
  nonce: string,
  appVersion: string,
): Uint8Array {
  return new TextEncoder().encode(
    `TKAUTO-${action}-v1\n${deviceId}\n${issuedAt}\n${nonce}\n${appVersion}`,
  );
}

export async function importDevicePublicKey(pem: string): Promise<CryptoKey> {
  const body = pem
    .replace(/-----BEGIN [A-Z ]+-----/g, "")
    .replace(/-----END [A-Z ]+-----/g, "")
    .replace(/\s+/g, "");
  if (!body) throw new Error("PEM khoa thiet bi rong");
  const der = Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
  // extractable = true vi `expectedDeviceId` phai xuat lai dang raw de bam.
  return await crypto.subtle.importKey("spki", der, "Ed25519", true, [
    "verify",
  ]);
}

/** `device_` + sha256(khoa cong khai raw)[..40]. Giong `expected_device_id`. */
export async function expectedDeviceId(key: CryptoKey): Promise<string> {
  const raw = new Uint8Array(await crypto.subtle.exportKey("raw", key));
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", raw));
  return `device_${toHex(digest).slice(0, 40)}`;
}

/**
 * So sanh hai chuoi trong thoi gian khong phu thuoc noi dung.
 *
 * ⛔ Python dung `hmac.compare_digest` cho cho nay; `===` cua JS thoat som o
 * byte dau khac nhau. Voi device_id thi ro ri thoi gian gan nhu vo hai, nhung
 * ham nay cung duoc dung o cho khac va de mot `===` o day la de san mot cai
 * bay cho lan sao chep sau.
 */
export function constantTimeEquals(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) {
    diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
  }
  return diff === 0;
}

export async function verifyDeviceProof(
  key: CryptoKey,
  canonical: Uint8Array,
  signature: string,
): Promise<boolean> {
  let raw: Uint8Array;
  try {
    raw = b64urlDecode(signature);
  } catch {
    return false;
  }
  if (raw.length !== 64) return false;
  return await crypto.subtle.verify("Ed25519", key, raw, canonical);
}

export async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return toHex(new Uint8Array(digest));
}
