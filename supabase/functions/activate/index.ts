// POST /functions/v1/activate - doi khoa license + bang chung thiet bi lay mot
// lease da ky. Ban Edge Function cua `control_plane/app/main.py:activate`.
//
// ⛔ THU TU CAC BUOC LA SAO Y BAN PYTHON, va thu tu do co ly do:
//   1. kiem phien ban client   -> khong ton mot truy van nao
//   2. kiem device_id khop khoa -> chi la mot phep bam
//   3. kiem CHU KY thiet bi     -> chi la crypto
//   4. roi moi goi DB, va DB moi chiem nonce
// Dao buoc 3 va 4 la mo duong cho mot request chu ky rac dot nonce cua mot
// request that (`enforce_fresh_proof` cua ban Python cung nam sau
// `verify_device_proof` vi the).

import {
  canonicalDeviceProof,
  constantTimeEquals,
  digestLicenseKey,
  expectedDeviceId,
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

interface ActivatePayload {
  device_id: string;
  issued_at: number;
  nonce: string;
  signature: string;
  app_version: string;
  license_key: string;
  device_public_key_pem: string;
}

const SPEC = {
  ...DEVICE_PROOF_SPEC,
  license_key: { kind: "string", min: 20, max: 128 },
  device_public_key_pem: { kind: "string", min: 80, max: 2_000 },
} as const;

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "POST") {
    return jsonResponse(405, { detail: "Method not allowed." });
  }

  let config;
  try {
    config = await loadConfig();
  } catch (error) {
    // ⛔ KHONG DOI CAU LOI CAU HINH RA NGOAI. No noi ten bien moi truong va do
    // dai secret. Ghi vao log cua function, tra ve mot cau trung tinh.
    console.error("cau hinh sai:", error);
    return jsonResponse(500, { detail: "Server is misconfigured." });
  }

  let payload: ActivatePayload;
  try {
    payload = parseStrict<ActivatePayload>(
      await readJsonBody(request),
      SPEC as unknown as Parameters<typeof parseStrict>[1],
    );
  } catch (error) {
    return jsonResponse(422, { detail: `Invalid request: ${(error as Error).message}` });
  }

  const versionVerdict = clientVersionVerdict(config, payload.app_version);
  if (versionVerdict) return versionVerdict;

  let deviceKey: CryptoKey;
  try {
    deviceKey = await importDevicePublicKey(payload.device_public_key_pem);
  } catch {
    return jsonResponse(400, { detail: "Invalid device public key." });
  }

  if (!constantTimeEquals(await expectedDeviceId(deviceKey), payload.device_id)) {
    return jsonResponse(400, {
      detail: "Device ID does not match the supplied public key.",
    });
  }

  const proofOk = await verifyDeviceProof(
    deviceKey,
    canonicalDeviceProof(
      "ACTIVATE",
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

  const keyDigest = await digestLicenseKey(payload.license_key, config.pepper);
  if (keyDigest === null) {
    return jsonResponse(401, { detail: "License key is invalid." });
  }

  // Giong `enforce_fresh_proof`: context la `activate:<device_id>`, va digest
  // la sha256("<context>\n<issued_at>\n<nonce>").
  const nonceDigest = await sha256Hex(
    `activate:${payload.device_id}\n${payload.issued_at}\n${payload.nonce}`,
  );

  let verdict: Record<string, unknown>;
  try {
    verdict = await callRpc(config, "tkauto_activate", {
      p_key_digest: keyDigest,
      p_device_id: payload.device_id,
      p_public_key_pem: payload.device_public_key_pem,
      p_nonce_digest: nonceDigest,
      p_nonce_ttl: config.maxClockSkewSeconds * 2,
      p_rate_bucket: clientBucket(request, "activate"),
      p_rate_limit: config.activationRateLimitPerMinute,
      p_rate_window: 60,
    });
  } catch (error) {
    console.error("tkauto_activate that bai:", error);
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
