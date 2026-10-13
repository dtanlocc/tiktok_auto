// POST /functions/v1/releases/check
//
// ⛔ FUNCTION NAY LA DIEU KIEN DE APP CHAY, khong phai mot tinh nang them.
// `check_backend_update` (lib.rs:833) la duong DUY NHAT launcher lay backend
// binary ve. Neu no tra `update_available: false` khi may chua co backend thi
// `launch_backend` goi `load_backend_pointer` roi chet voi "Backend binary
// chua duoc cai." - NGAY LAN CHAY DAU, khong phai sau khi het han. Ban dau
// function nay luon tra false va do la mot ban licensed khong bao gio khoi
// dong duoc.
//
// ⛔ CHON BAN PHAT HANH O DAY, KHONG O SQL. Postgres tra ve danh sach ung
// vien; viec so sanh semver (ke ca prerelease) dung `compareSemver` trong
// lease.ts - ham DA duoc do tung truong hop voi `_compare_semver` cua Python
// bang `test_supabase_lease_parity.py`. Viet ban so sanh thu ba trong plpgsql
// la tu them mot cho de lech.
//
// ⛔ VA KHOA KY RELEASE KHONG O DAY. `signed_manifest` duoc ky o may operator
// roi luu vao DB; function nay phat lai chuoi do. Chiem duoc Supabase khong
// ky duoc mot manifest moi, tuc khong gui duoc ban cai doc cho khach.

import { compareSemver } from "../_shared/lease.ts";
import { issueDownloadGrant } from "../_shared/grant.ts";
import {
  canonicalDeviceProof,
  importDevicePublicKey,
  sha256Hex,
  verifyDeviceProof,
} from "../_shared/proof.ts";
import {
  callRpc,
  clientBucket,
  DEVICE_PROOF_SPEC,
  jsonResponse,
  loadConfig,
  parseStrict,
  readJsonBody,
  requireDelivery,
  verdictResponse,
} from "../_shared/service.ts";

interface ReleaseCheckPayload {
  device_id: string;
  issued_at: number;
  nonce: string;
  signature: string;
  app_version: string;
  license_id: string;
  component: string;
  current_version: string;
  target: string;
}

interface Candidate {
  id: string;
  version: string;
  minimum_launcher_version: string;
  signed_manifest: string;
}

const SPEC = {
  ...DEVICE_PROOF_SPEC,
  license_id: { kind: "string", min: 3, max: 128 },
  component: { kind: "string", min: 6, max: 16, pattern: /^(backend|desktop)$/ },
  current_version: { kind: "string", min: 5, max: 64 },
  target: { kind: "string", min: 5, max: 32, pattern: /^windows-x86_64$/ },
} as const;

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "POST") {
    return jsonResponse(405, { detail: "Method not allowed." });
  }
  if (!new URL(request.url).pathname.replace(/\/+$/, "").endsWith("/check")) {
    return jsonResponse(404, { detail: "Not found." });
  }

  let config;
  try {
    config = await loadConfig();
    requireDelivery(config);
  } catch (error) {
    console.error("cau hinh sai:", error);
    return jsonResponse(500, { detail: "Server is misconfigured." });
  }

  let payload: ReleaseCheckPayload;
  try {
    payload = parseStrict<ReleaseCheckPayload>(
      await readJsonBody(request),
      SPEC as unknown as Parameters<typeof parseStrict>[1],
    );
  } catch (error) {
    return jsonResponse(422, { detail: `Invalid request: ${(error as Error).message}` });
  }

  // ⛔ KHONG kiem san phien ban client o day. Ban Python cung khong
  // (`check_release` khong goi `enforce_client_version`), va do la dung: mot
  // client qua cu phai duoc BIET la co ban moi, chu khong bi tu choi o dung
  // cho no hoi ve ban moi.

  let lookup: Record<string, unknown>;
  try {
    lookup = await callRpc(config, "tkauto_renew_device", {
      p_device_id: payload.device_id,
      p_license_id: payload.license_id,
    });
  } catch (error) {
    console.error("tkauto_renew_device that bai:", error);
    return jsonResponse(503, { detail: "License service is unavailable." });
  }
  if (lookup.ok !== true) {
    return verdictResponse(String(lookup.code ?? "device_invalid"));
  }

  let deviceKey: CryptoKey;
  try {
    deviceKey = await importDevicePublicKey(String(lookup.public_key_pem));
  } catch (error) {
    console.error("khoa thiet bi trong DB khong doc duoc:", error);
    return jsonResponse(500, { detail: "Stored device key is unreadable." });
  }

  const action =
    `RELEASE:${payload.component}:${payload.current_version}:${payload.target}`;
  const proofOk = await verifyDeviceProof(
    deviceKey,
    canonicalDeviceProof(
      action,
      payload.device_id,
      payload.issued_at,
      payload.nonce,
      payload.app_version,
    ),
    payload.signature,
  );
  if (!proofOk) return jsonResponse(401, { detail: "Device proof is invalid." });

  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - payload.issued_at) > config.maxClockSkewSeconds) {
    return jsonResponse(401, {
      detail: "Device proof is outside the allowed time window.",
    });
  }

  const nonceDigest = await sha256Hex(
    `release:${payload.license_id}:${payload.device_id}:${payload.component}\n` +
      `${payload.issued_at}\n${payload.nonce}`,
  );

  let verdict: Record<string, unknown>;
  try {
    verdict = await callRpc(config, "tkauto_release_check", {
      p_device_id: payload.device_id,
      p_license_id: payload.license_id,
      p_nonce_digest: nonceDigest,
      p_nonce_ttl: config.maxClockSkewSeconds * 2,
      p_rate_bucket: clientBucket(request, "device"),
      p_rate_limit: config.deviceRateLimitPerMinute,
      p_rate_window: 60,
      p_component: payload.component,
      p_target: payload.target,
    });
  } catch (error) {
    console.error("tkauto_release_check that bai:", error);
    return jsonResponse(503, { detail: "License service is unavailable." });
  }
  if (verdict.ok !== true) {
    return verdictResponse(String(verdict.code ?? ""));
  }

  // Giong ban Python: chi nhung ban MOI HON ban dang cai, va chi nhung ban ma
  // launcher hien tai du moi de chay.
  const candidates = (verdict.releases as Candidate[] | undefined) ?? [];
  let best: Candidate | null = null;
  try {
    for (const item of candidates) {
      if (compareSemver(item.version, payload.current_version) <= 0) continue;
      if (compareSemver(payload.app_version, item.minimum_launcher_version) < 0) {
        continue;
      }
      if (best === null || compareSemver(item.version, best.version) > 0) {
        best = item;
      }
    }
  } catch (error) {
    // Mot phien ban sai dinh dang trong DB la loi cua minh, khong phai cua
    // khach - nhung van phai tra loi, va 422 la ma ban Python dung.
    console.error("phien ban trong so dang ky khong hop le:", error);
    return jsonResponse(422, { detail: "Release version is invalid." });
  }

  if (best === null) {
    return jsonResponse(200, { update_available: false });
  }

  const grant = await issueDownloadGrant(config.downloadGrantSecret, {
    artifactId: best.id,
    deviceId: payload.device_id,
    ttlSeconds: config.downloadGrantTtlSeconds,
  });

  return jsonResponse(200, {
    update_available: true,
    signed_manifest: best.signed_manifest,
    download_grant: grant.token,
    grant_expires_at: grant.expiresAt,
  });
});
