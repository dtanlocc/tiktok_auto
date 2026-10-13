// POST /functions/v1/releases/check
//
// ⛔ FUNCTION NAY LA DIEU KIEN DE APP CHAY DUOC, khong phai mot tinh nang
// thêm. `SecureBootstrap.tsx:62` goi `await invoke('check_backend_update')`
// KHONG co try/catch, ngay truoc `launch_backend`. Thieu function nay thi
// duong `{base}/v1/releases/check` tra 404, invoke nem loi, phase thanh
// 'error', va backend khong bao gio chay - ke ca khi kich hoat da thanh cong.
//
// ⛔ VA NO LUON TRA `update_available: false`. Do la cau tra loi DUNG chu
// khong phai mot stub: khong co ban phat hanh nao duoc ghi nhan tren Supabase,
// vi phat hanh can manifest da ky bang KHOA RELEASE - khoa do khong duoc dua
// len may cua ben thu ba. Hai hau qua (khong auto-update, khong update bat
// buoc) duoc ghi o dau file
// supabase/migrations/20261013000200_release_check.sql va trong
// docs/SUPABASE-KEY.md.
//
// ⛔ VAN KIEM BANG CHUNG THIET BI DU CHANG CAP GI. Mot endpoint tra loi cho
// bat ky ai la mot endpoint khong ai kiem, va no se duoc sao chep lam mau cho
// endpoint sau. Ngoai ra ban Python kiem day du o cho nay, nen bo di la hai
// ban cu xu khac nhau o mot thu de thay.

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

  // ⛔ KHONG kiem phien ban client o day. Ban Python cung khong
  // (`check_release` khong goi `enforce_client_version`), va do la dung: mot
  // client qua cu phai duoc BIET la co ban moi, chu khong phai bi tu choi o
  // dung cho no hoi ve ban moi.

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

  // Giong ban Python: action mang ca component, phien ban hien tai va target,
  // nen mot bang chung ky cho lan kiem nay khong dung lai cho lan khac.
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
    });
  } catch (error) {
    console.error("tkauto_release_check that bai:", error);
    return jsonResponse(503, { detail: "License service is unavailable." });
  }
  if (verdict.ok !== true) {
    return verdictResponse(String(verdict.code ?? ""));
  }

  // ⛔ BA TRUONG CON LAI DUOC BO HAN, khong gui null. `ReleaseCheckResponse`
  // trong lib.rs khai chung la `Option<_>` nen thieu la None; con gui null thi
  // cung None - nhung bo han thi phan hoi noi dung mot dieu: khong co gi de
  // tai. Launcher thoat ngay o `if !response.update_available`.
  return jsonResponse(200, { update_available: false });
});
