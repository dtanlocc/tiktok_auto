// POST /functions/v1/lease/renew - gia han lease cho mot thiet bi da kich hoat.
// Ban Edge Function cua `control_plane/app/main.py:renew`.
//
// ⛔ TEN FUNCTION LA `lease`, KHONG PHAI `renew`, VA DO KHONG PHAI TUY HUNG.
// Launcher dung URL cung: `{base}/v1/lease/renew` (`lib.rs:663`). Supabase dinh
// tuyen `/functions/v1/<ten>/...` ve function `<ten>` va giu phan duong con
// lai, nen dat ten `lease` lam `TKAUTO_CONTROL_PLANE_URL=https://<ref>.supabase
// .co/functions` khop ca hai endpoint ma KHONG phai sua mot dong Rust nao. Dat
// ten `renew` thi phai sua launcher va build lai ban desktop.
//
// ⛔ KHAC activate O MOT DIEM QUAN TRONG: khong nhan khoa cong khai tu request.
// Chu ky duoc kiem bang khoa DA LUU trong DB tu luc kich hoat. Nhan khoa tu
// request roi kiem chu ky bang chinh no la tu kiem chinh minh - ai cung ky
// duoc bang khoa rieng cua ho. Vi the phai goi DB mot lan de LAY KHOA truoc,
// roi moi kiem chu ky, roi moi goi lan hai de chiem nonce va gia han.
//
// Hai lan goi DB nay la gia phai tra cho viec khong tin khoa tu client. Lan
// dau khong chiem nonce va khong doi trang thai gi, nen no khong the bi dung
// de dot nonce cua ai.

import {
  canonicalDeviceProof,
  importDevicePublicKey,
  sha256Hex,
  verifyDeviceProof,
} from "../_shared/proof.ts";
import {
  callRpc,
  clientBucket,
  clientVersionVerdict,
  DEVICE_PROOF_SPEC,
  issueLease,
  jsonResponse,
  type LicenseRow,
  loadConfig,
  parseStrict,
  readJsonBody,
  verdictResponse,
} from "../_shared/service.ts";

interface RenewPayload {
  device_id: string;
  issued_at: number;
  nonce: string;
  signature: string;
  app_version: string;
  license_id: string;
}

const SPEC = {
  ...DEVICE_PROOF_SPEC,
  license_id: { kind: "string", min: 3, max: 128 },
} as const;

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "POST") {
    return jsonResponse(405, { detail: "Method not allowed." });
  }
  // ⛔ CHI NHAN DUNG DUONG CON `/renew`. Function `lease` nhan MOI duong bat
  // dau bang `/functions/v1/lease/`, nen khong chan thi `/lease/`,
  // `/lease/bat-ky-gi` deu chay vao day va duoc xu ly nhu mot yeu cau gia han.
  // Mot endpoint tra loi o nhung duong khong ai dinh nghia la mot endpoint
  // khong ai kiem.
  if (!new URL(request.url).pathname.replace(/\/+$/, "").endsWith("/renew")) {
    return jsonResponse(404, { detail: "Not found." });
  }

  let config;
  try {
    config = await loadConfig();
  } catch (error) {
    console.error("cau hinh sai:", error);
    return jsonResponse(500, { detail: "Server is misconfigured." });
  }

  let payload: RenewPayload;
  try {
    payload = parseStrict<RenewPayload>(
      await readJsonBody(request),
      SPEC as unknown as Parameters<typeof parseStrict>[1],
    );
  } catch (error) {
    return jsonResponse(422, { detail: `Invalid request: ${(error as Error).message}` });
  }

  const versionVerdict = clientVersionVerdict(config, payload.app_version);
  if (versionVerdict) return versionVerdict;

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
    // Khoa nay do CHINH TA ghi vao DB luc kich hoat, nen khong doc duoc la DB
    // bi sua tay chu khong phai khach gui rac.
    console.error("khoa thiet bi trong DB khong doc duoc:", error);
    return jsonResponse(500, { detail: "Stored device key is unreadable." });
  }

  const proofOk = await verifyDeviceProof(
    deviceKey,
    canonicalDeviceProof(
      "RENEW",
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

  // Giong ban Python: context la `renew:<license_id>:<device_id>`.
  const nonceDigest = await sha256Hex(
    `renew:${payload.license_id}:${payload.device_id}\n${payload.issued_at}\n${payload.nonce}`,
  );

  let verdict: Record<string, unknown>;
  try {
    verdict = await callRpc(config, "tkauto_renew", {
      p_device_id: payload.device_id,
      p_license_id: payload.license_id,
      p_nonce_digest: nonceDigest,
      p_nonce_ttl: config.maxClockSkewSeconds * 2,
      p_rate_bucket: clientBucket(request, "device"),
      p_rate_limit: config.deviceRateLimitPerMinute,
      p_rate_window: 60,
    });
  } catch (error) {
    console.error("tkauto_renew that bai:", error);
    return jsonResponse(503, { detail: "License service is unavailable." });
  }

  if (verdict.ok !== true) {
    return verdictResponse(String(verdict.code ?? ""));
  }

  const lease = await issueLease(
    config,
    verdict.license as LicenseRow,
    String(verdict.device_id),
  );
  if (lease === null) {
    return jsonResponse(403, { detail: "License has expired." });
  }
  return jsonResponse(200, lease);
});
