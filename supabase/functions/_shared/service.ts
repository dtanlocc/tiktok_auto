// Phan dung chung cua hai Edge Function: cau hinh, kiem dau vao, goi RPC, phat
// lease.

import { type LeaseClaims, compareSemver, importLeasePrivateKey, signLease } from "./lease.ts";

// ⛔ GIOI HAN THAN BODY. Ban Python co `max_request_body_bytes` (128 KiB) va
// Edge Function khong co gioi han nao san: thieu cho nay thi mot POST vai
// tram MB se duoc doc het vao bo nho truoc khi bi tu choi vi sai dinh dang.
const MAX_BODY_BYTES = 128 * 1024;

export interface Config {
  pepper: string;
  leaseKeyId: string;
  leaseKey: CryptoKey;
  leaseTtlSeconds: number;
  maxClockSkewSeconds: number;
  minimumClientVersion: string;
  minimumBackendVersion: string;
  activationRateLimitPerMinute: number;
  deviceRateLimitPerMinute: number;
  supabaseUrl: string;
  serviceRoleKey: string;
  /** ⛔ Rong khi chua bat phan phat ban phat hanh - xem `requireDelivery`. */
  downloadGrantSecret: string;
  downloadGrantTtlSeconds: number;
  /**
   * Cloudflare R2 - noi chua file backend.
   *
   * ⛔ KHONG DUNG Supabase Storage, va do la mot quyet dinh bi BUOC: Free plan
   * cua Supabase chan upload o 50 MB va khong nang duoc tu dashboard, trong
   * khi backend 0.1.12 la 50,7 MiB. R2 cho 10 GB luu, khong tinh egress, mot
   * PUT toi 5 GB. Doi duoc cho chua ma khong phai ky lai manifest nao la vi
   * `download_url` tro vao function `artifacts` chu khong tro thang vao Storage.
   */
  r2AccountId: string;
  r2Bucket: string;
  r2AccessKeyId: string;
  r2SecretAccessKey: string;
}

function required(name: string): string {
  const value = (globalThis as { Deno?: { env: { get(k: string): string | undefined } } })
    .Deno?.env.get(name) ?? (globalThis as { process?: { env: Record<string, string | undefined> } })
      .process?.env[name];
  const trimmed = (value ?? "").trim();
  if (!trimmed) throw new Error(`Thieu bien moi truong ${name}`);
  return trimmed;
}

function optionalInt(name: string, fallback: number): number {
  const raw = (globalThis as { Deno?: { env: { get(k: string): string | undefined } } })
    .Deno?.env.get(name) ?? (globalThis as { process?: { env: Record<string, string | undefined> } })
      .process?.env[name];
  if (raw === undefined || raw.trim() === "") return fallback;
  const parsed = Number(raw);
  if (!Number.isSafeInteger(parsed)) {
    throw new Error(`${name} phai la so nguyen`);
  }
  return parsed;
}

function optionalText(name: string): string {
  const raw = (globalThis as { Deno?: { env: { get(k: string): string | undefined } } })
    .Deno?.env.get(name) ?? (globalThis as { process?: { env: Record<string, string | undefined> } })
      .process?.env[name];
  return (raw ?? "").trim();
}

let cached: Config | null = null;

/**
 * Doc va KIEM cau hinh, giong `ControlPlaneSettings.validate_runtime`.
 *
 * ⛔ KIEM O DAY CHU KHONG O LAN DUNG DAU TIEN. Mot pepper dai 8 ky tu hoac mot
 * TTL mot nam khong lam request nao that bai - no chi am tham lam he thong yeu
 * di, va se khong ai phat hien. Sai cau hinh phai lam function khong chay duoc.
 */
export async function loadConfig(): Promise<Config> {
  if (cached) return cached;
  const pepper = required("TKAUTO_LICENSE_KEY_PEPPER");
  if (pepper.length < 32) {
    throw new Error("TKAUTO_LICENSE_KEY_PEPPER phai dai it nhat 32 ky tu");
  }
  const leaseKeyId = required("TKAUTO_LEASE_SIGNING_KEY_ID");
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$/.test(leaseKeyId)) {
    throw new Error("TKAUTO_LEASE_SIGNING_KEY_ID sai dinh dang");
  }
  const leaseTtlSeconds = optionalInt("TKAUTO_LEASE_TTL_SECONDS", 3_600);
  if (leaseTtlSeconds < 300 || leaseTtlSeconds > 7 * 86_400) {
    throw new Error("TKAUTO_LEASE_TTL_SECONDS phai trong [300, 604800]");
  }
  const maxClockSkewSeconds = optionalInt("TKAUTO_MAX_CLOCK_SKEW_SECONDS", 300);
  if (maxClockSkewSeconds < 30 || maxClockSkewSeconds > 3_600) {
    throw new Error("TKAUTO_MAX_CLOCK_SKEW_SECONDS phai trong [30, 3600]");
  }
  const minimumClientVersion = required("TKAUTO_MINIMUM_CLIENT_VERSION");
  const minimumBackendVersion = required("TKAUTO_MINIMUM_BACKEND_VERSION");
  // Goi de no nem ngay neu sai dinh dang semver.
  compareSemver(minimumClientVersion, minimumClientVersion);
  compareSemver(minimumBackendVersion, minimumBackendVersion);

  cached = {
    pepper,
    leaseKeyId,
    leaseKey: await importLeasePrivateKey(
      required("TKAUTO_LEASE_SIGNING_PRIVATE_KEY_PEM"),
    ),
    leaseTtlSeconds,
    maxClockSkewSeconds,
    minimumClientVersion,
    minimumBackendVersion,
    activationRateLimitPerMinute: optionalInt(
      "TKAUTO_ACTIVATION_RATE_LIMIT_PER_MINUTE",
      10,
    ),
    deviceRateLimitPerMinute: optionalInt(
      "TKAUTO_DEVICE_RATE_LIMIT_PER_MINUTE",
      120,
    ),
    supabaseUrl: required("SUPABASE_URL").replace(/\/+$/, ""),
    serviceRoleKey: required("SUPABASE_SERVICE_ROLE_KEY"),
    // ⛔ TUY CHON, va `requireDelivery` ben duoi la cho bat buoc no. Hai
    // function activate/lease khong can bi mat nay, nen doi no o `loadConfig`
    // se lam ca he thong khong chay duoc chi vi chua bat phan phat ban phat
    // hanh. Nhung `releases`/`artifacts` thi KHONG duoc chay voi mot bi mat
    // rong - mot HMAC khoa rong la mot ve ai cung ky duoc.
    downloadGrantSecret: optionalText("TKAUTO_DOWNLOAD_GRANT_SECRET"),
    downloadGrantTtlSeconds: optionalInt("TKAUTO_DOWNLOAD_GRANT_TTL_SECONDS", 300),
    r2AccountId: optionalText("TKAUTO_R2_ACCOUNT_ID"),
    r2Bucket: optionalText("TKAUTO_R2_BUCKET") || "releases",
    r2AccessKeyId: optionalText("TKAUTO_R2_ACCESS_KEY_ID"),
    r2SecretAccessKey: optionalText("TKAUTO_R2_SECRET_ACCESS_KEY"),
  };
  if (
    cached.downloadGrantTtlSeconds < 60 ||
    cached.downloadGrantTtlSeconds > 900
  ) {
    throw new Error("TKAUTO_DOWNLOAD_GRANT_TTL_SECONDS phai trong [60, 900]");
  }
  return cached;
}

/**
 * Doi bi mat ve tai, cho hai function phat hanh.
 *
 * ⛔ TU CHOI KHOA NGAN, khong chi khoa rong. Mot HMAC voi khoa 8 ky tu la mot
 * ve do duoc; va giong ban Python (`validate_runtime` doi >= 32 ky tu), sai
 * cau hinh phai lam function chet chu khong phai chay yeu hon.
 */
export function requireDelivery(config: Config): void {
  if (config.downloadGrantSecret.length < 32) {
    throw new Error(
      "TKAUTO_DOWNLOAD_GRANT_SECRET phai dai it nhat 32 ky tu de bat phan phat ban phat hanh",
    );
  }
  // ⛔ DOI DU CA BON, khong chi "co cai nao thi dung cai do". Thieu mot bien
  // thi `presignR2Get` van ky ra mot URL - mot URL tro vao host rong hoac ky
  // bang khoa rong - va R2 tra 403 khong noi gi ve nguyen nhan. Dung lai o day
  // thi thong bao noi dung ten bien thieu.
  const missing = (
    [
      ["TKAUTO_R2_ACCOUNT_ID", config.r2AccountId],
      ["TKAUTO_R2_BUCKET", config.r2Bucket],
      ["TKAUTO_R2_ACCESS_KEY_ID", config.r2AccessKeyId],
      ["TKAUTO_R2_SECRET_ACCESS_KEY", config.r2SecretAccessKey],
    ] as const
  ).filter(([, value]) => !value).map(([name]) => name);
  if (missing.length > 0) {
    throw new Error(`Thieu cau hinh R2: ${missing.join(", ")}`);
  }
  if (!/^[0-9a-f]{32}$/.test(config.r2AccountId)) {
    throw new Error("TKAUTO_R2_ACCOUNT_ID phai la 32 ky tu hex");
  }
}

/**
 * ⛔ `no-store` KHONG PHAI TRANG TRI. Than cua phan hoi thanh cong CHINH LA
 * lease - mot token ky cho dung mot thiet bi. Ban Python dat header nay cho moi
 * phan hoi trong middleware; bo qua o day thi mot proxy tren duong di duoc
 * phep giu lai va phat lai no. Bon header con lai la sao y ban Python.
 */
export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer",
      "x-frame-options": "DENY",
    },
  });
}

/**
 * IP cua khach, de lam xo chong do tan so - giong `{group}:{client_host}` cua
 * ban Python.
 *
 * ⛔ LAY IP DAU TIEN TRONG x-forwarded-for, va chi tin no vi Edge Function
 * luon nam sau proxy cua Supabase. Noi khac thi header nay do client tu dat
 * duoc, va luc do chong do tan so bi vo hieu bang mot dong header.
 */
export function clientBucket(request: Request, group: string): string {
  const forwarded = request.headers.get("x-forwarded-for") ?? "";
  const first = forwarded.split(",")[0].trim();
  return `${group}:${first || "unknown"}`;
}

/**
 * ⛔ THONG BAO LOI KHONG NOI RO HON MUC CAN THIET. Ban Python tra ve cau nhu
 * "License key is invalid." va "License is revoked or expired." - hai cau khac
 * nhau cho hai tinh huong khac nhau, nen nguoi do key biet duoc key nao TON
 * TAI nhung het han. Giu nguyen cach do o day de hai ban cu xu giong nhau, va
 * ghi lai rang day la mot ro ri co y chap nhan: khach can biet key cua minh
 * het han chu khong phai nhap sai.
 */
export const CODE_STATUS: Record<string, [number, string]> = {
  rate_limited: [429, "Too many attempts. Try again in a minute."],
  nonce_replayed: [409, "Device proof nonce was already used."],
  license_invalid: [401, "License key is invalid."],
  license_revoked_or_expired: [403, "License is revoked or expired."],
  device_conflict: [409, "Device identity is already bound differently."],
  device_limit: [403, "License device limit has been reached."],
  device_revoked: [403, "Device activation was revoked."],
  device_invalid: [403, "Device activation is not valid."],
};

export function verdictResponse(code: string): Response {
  const mapped = CODE_STATUS[code] ?? [403, "Request was refused."];
  return jsonResponse(mapped[0], { detail: mapped[1] });
}

export async function readJsonBody(request: Request): Promise<unknown> {
  const declared = request.headers.get("content-length");
  if (declared !== null && Number(declared) > MAX_BODY_BYTES) {
    throw new Error("body qua lon");
  }
  const raw = new Uint8Array(await request.arrayBuffer());
  if (raw.byteLength > MAX_BODY_BYTES) throw new Error("body qua lon");
  return JSON.parse(new TextDecoder().decode(raw));
}

type FieldSpec =
  | { kind: "string"; min: number; max: number; pattern?: RegExp }
  | { kind: "int"; min: number; max: number };

/**
 * Kiem dau vao theo dung cach `StrictModel` cua Pydantic lam, ke ca
 * `extra="forbid"`.
 *
 * ⛔ TU CHOI TRUONG LA, KHONG BO QUA. `schemas.py` dat extra="forbid" co ly do:
 * mot truong go sai ten (`devide_id`) se duoc bo qua am tham va truong that
 * nhan gia tri mac dinh, bien mot request sai thanh mot request "hop le" voi
 * y nghia khac. Bo qua truong la o day la lam yeu hon ban Python.
 */
export function parseStrict<T>(
  payload: unknown,
  spec: Record<string, FieldSpec>,
): T {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new Error("body phai la mot object JSON");
  }
  const source = payload as Record<string, unknown>;
  for (const key of Object.keys(source)) {
    if (!(key in spec)) throw new Error(`truong khong duoc phep: ${key}`);
  }
  const out: Record<string, unknown> = {};
  for (const [key, rule] of Object.entries(spec)) {
    const value = source[key];
    if (rule.kind === "string") {
      if (typeof value !== "string") throw new Error(`${key} phai la chuoi`);
      if (value.length < rule.min || value.length > rule.max) {
        throw new Error(`${key} co do dai khong hop le`);
      }
      if (rule.pattern && !rule.pattern.test(value)) {
        throw new Error(`${key} sai dinh dang`);
      }
      out[key] = value;
    } else {
      if (typeof value !== "number" || !Number.isSafeInteger(value)) {
        throw new Error(`${key} phai la so nguyen`);
      }
      if (value < rule.min || value > rule.max) {
        throw new Error(`${key} ngoai khoang cho phep`);
      }
      out[key] = value;
    }
  }
  return out as T;
}

export const DEVICE_PROOF_SPEC: Record<string, FieldSpec> = {
  device_id: {
    kind: "string",
    min: 3,
    max: 128,
    pattern: /^[A-Za-z0-9][A-Za-z0-9._:-]+$/,
  },
  issued_at: { kind: "int", min: 0, max: 9_999_999_999 },
  nonce: { kind: "string", min: 16, max: 128, pattern: /^[A-Za-z0-9_-]+$/ },
  signature: {
    kind: "string",
    min: 40,
    max: 256,
    pattern: /^[A-Za-z0-9_-]+$/,
  },
  app_version: { kind: "string", min: 5, max: 64 },
};

export async function callRpc(
  config: Config,
  name: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const response = await fetch(`${config.supabaseUrl}/rest/v1/rpc/${name}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      apikey: config.serviceRoleKey,
      authorization: `Bearer ${config.serviceRoleKey}`,
    },
    body: JSON.stringify(args),
  });
  if (!response.ok) {
    throw new Error(`RPC ${name} that bai: ${response.status} ${await response.text()}`);
  }
  return (await response.json()) as Record<string, unknown>;
}

export interface LicenseRow {
  id: string;
  plan: string;
  features: string[];
  max_accounts: number;
  max_tabs: number;
  channel: string;
  expires_at: number;
}

/**
 * `app.start` dung dau, trung lap bi loai, thu tu con lai giu nguyen.
 *
 * ⛔ XUAT RA RIENG DE TEST CHAM DUOC VAO DUNG MA NAY. Ban Python lam
 * `dict.fromkeys(("app.start", *features))` - vua them vua giu thu tu vua bo
 * trung. `features` duoc KY nen thu tu la MOT PHAN CUA BYTE DUOC KY: dao thu
 * tu hay bo sot mot ban trung la ra token khac, va lease do khong verify duoc
 * o may khach. Day la cho de lech nhat giua hai ban, nen
 * `test_supabase_lease_parity.py` goi thang ham nay.
 */
export function effectiveFeatures(features: string[]): string[] {
  return [...new Set(["app.start", ...features])];
}

/** Phat lease, giong `control_plane/app/security.py:issue_lease`. */
export async function issueLease(
  config: Config,
  license: LicenseRow,
  deviceId: string,
): Promise<{ lease: string; expires_at: number; renew_after: number } | null> {
  const now = Math.floor(Date.now() / 1000);
  const expiresAt = Math.min(license.expires_at, now + config.leaseTtlSeconds);
  if (expiresAt <= now) return null;

  const features = effectiveFeatures(license.features);
  const jtiBytes = new Uint8Array(16);
  crypto.getRandomValues(jtiBytes);
  const jti = `lease_${[...jtiBytes].map((b) => b.toString(16).padStart(2, "0")).join("")}`;

  const claims: LeaseClaims = {
    protocol_version: 1,
    license_id: license.id,
    device_id: deviceId,
    plan: license.plan,
    features,
    max_accounts: license.max_accounts,
    max_tabs: license.max_tabs,
    channel: license.channel,
    minimum_version: config.minimumClientVersion,
    minimum_backend_version: config.minimumBackendVersion,
    issued_at: now,
    not_before: now - 5,
    expires_at: expiresAt,
    jti,
  };

  return {
    lease: await signLease(config.leaseKey, config.leaseKeyId, claims),
    expires_at: expiresAt,
    // ⛔ KET O `expiresAt - 1`. Launcher tu choi phan hoi khi
    // `renew_after >= expires_at` (`lib.rs`: "Thoi han lease trong phan hoi
    // khong nhat quan"), va `expiresAt` bi ket o han cua license trong khi
    // `ttl/2` thi khong - nen trong ttl/2 cuoi doi cua mot license, hai so dao
    // chieu. Ban song doi la `renew_after_for` trong
    // control_plane/app/security.py.
    renew_after: Math.min(
      now + Math.floor(config.leaseTtlSeconds / 2),
      expiresAt - 1,
    ),
  };
}

/** Giong `enforce_client_version`: duoi san la 426, sai dinh dang la 422. */
export function clientVersionVerdict(
  config: Config,
  appVersion: string,
): Response | null {
  let outdated: boolean;
  try {
    outdated = compareSemver(appVersion, config.minimumClientVersion) < 0;
  } catch {
    return jsonResponse(422, { detail: "Client version is invalid." });
  }
  if (outdated) {
    return jsonResponse(426, {
      detail: "Client version is below the required minimum.",
    });
  }
  return null;
}
