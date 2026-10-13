// Ve tai mot lan, dang y het `control_plane/app/security.py`.
//
// ⛔ BAN THU HAI CUA MOT THU, LAI NUA. Cung rui ro nhu lease.ts: ve do
// `releases` phat ra va do `artifacts` kiem lai, hai function khac nhau - nen
// mot byte lech trong JSON la moi lan tai deu 401, va loi do chi lo ra khi
// khach bam cap nhat. `test_supabase_download_grant_parity.py` so sanh tung
// byte voi `issue_download_grant` cua Python va kiem cheo ca hai chieu.
//
// ⛔ VA NO DUNG LAI `canonicalJson` CUA lease.ts, khong tu dung chuoi JSON
// rieng. Python dung `json.dumps(claims, sort_keys=True, separators=(",",":"))`
// o ca hai cho, tuc cung mot phep canonical - nen dung cung mot ham, de khong
// co kha nang hai cho trong CUNG ban TypeScript lech nhau.

import { b64urlDecode, b64urlEncode, canonicalJson } from "./lease.ts";

export interface GrantClaims {
  artifact_id: string;
  device_id: string;
  expires_at: number;
  nonce: string;
  v: 1;
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  return await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
}

export async function issueDownloadGrant(
  secret: string,
  options: { artifactId: string; deviceId: string; ttlSeconds: number },
): Promise<{ token: string; expiresAt: number }> {
  const expiresAt = Math.floor(Date.now() / 1000) + options.ttlSeconds;
  const nonceBytes = new Uint8Array(18);
  crypto.getRandomValues(nonceBytes);
  const claims: GrantClaims = {
    artifact_id: options.artifactId,
    device_id: options.deviceId,
    expires_at: expiresAt,
    // `secrets.token_urlsafe(18)` ben Python -> 24 ky tu base64url khong dem.
    nonce: b64urlEncode(nonceBytes),
    v: 1,
  };
  const payload = b64urlEncode(
    canonicalJson(claims as unknown as Record<string, unknown>),
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    await hmacKey(secret),
    new TextEncoder().encode(payload),
  );
  return {
    token: `${payload}.${b64urlEncode(new Uint8Array(signature))}`,
    expiresAt,
  };
}

/**
 * Kiem ve. Tra ve claims, hoac null neu bat hop le.
 *
 * ⛔ SO SANH CHU KY TRONG THOI GIAN CO DINH. Python dung
 * `hmac.compare_digest`; mot vong `===` theo byte thoat som o byte dau khac
 * nhau, va day la cho mot ke co the do dan chu ky dung.
 *
 * ⛔ VA TAP KHOA PHAI KHOP CHINH XAC, khong chi "co du khoa can". Python kiem
 * `set(claims) != {...}` - tuc mot khoa LA cung bi tu choi. De lot thi mot ve
 * co them truong la van qua, va truong do co the mang y nghia o ban sau.
 */
export async function verifyDownloadGrant(
  secret: string,
  token: string,
  artifactId: string,
): Promise<GrantClaims | null> {
  if (!token || token.length > 4096) return null;
  const parts = token.split(".");
  if (parts.length !== 2) return null;
  const [payload, encodedSignature] = parts;

  let supplied: Uint8Array;
  try {
    supplied = b64urlDecode(encodedSignature);
  } catch {
    return null;
  }
  const expected = new Uint8Array(
    await crypto.subtle.sign(
      "HMAC",
      await hmacKey(secret),
      new TextEncoder().encode(payload),
    ),
  );
  if (supplied.length !== expected.length) return null;
  let diff = 0;
  for (let i = 0; i < expected.length; i += 1) diff |= supplied[i] ^ expected[i];
  if (diff !== 0) return null;

  let claims: Record<string, unknown>;
  try {
    claims = JSON.parse(new TextDecoder().decode(b64urlDecode(payload)));
  } catch {
    return null;
  }
  if (typeof claims !== "object" || claims === null || Array.isArray(claims)) {
    return null;
  }
  const keys = Object.keys(claims).sort().join(",");
  if (keys !== "artifact_id,device_id,expires_at,nonce,v") return null;
  if (claims.v !== 1) return null;
  if (claims.artifact_id !== artifactId) return null;
  if (typeof claims.device_id !== "string" || !claims.device_id) return null;
  if (typeof claims.nonce !== "string" || !claims.nonce) return null;
  if (
    typeof claims.expires_at !== "number" ||
    !Number.isSafeInteger(claims.expires_at) ||
    Math.floor(Date.now() / 1000) >= claims.expires_at
  ) {
    return null;
  }
  return claims as unknown as GrantClaims;
}

export async function grantDigest(token: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(token),
  );
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
