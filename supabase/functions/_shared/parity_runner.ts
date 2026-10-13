// Cong cu cho `backend/tests/test_supabase_lease_parity.py`: doc mot mang
// phep toan tu stdin, chay CHINH nhung module ma Edge Function dung, in ket
// qua ra stdout dang JSON.
//
// ⛔ FILE NAY KHONG ChAY TREN SUPABASE. No ton tai de ban TypeScript va ban
// Python duoc do bang nhau tren cung mot may, bang Node da co san - khong phai
// cai Deno hay Supabase CLI chi de chay mot bai test. Vi no import dung
// `lease.ts` va `proof.ts` ma Edge Function import, mot thay doi lam lech chu
// ky se lam test Python do ngay, chu khong doi tien khach bao loi.

import { canonicalJson, compareSemver, importLeasePrivateKey, signLease } from "./lease.ts";
import { effectiveFeatures } from "./service.ts";
import { grantDigest, issueDownloadGrant, verifyDownloadGrant } from "./grant.ts";
import {
  canonicalDeviceProof,
  digestLicenseKey,
  expectedDeviceId,
  importDevicePublicKey,
} from "./proof.ts";

type Op =
  | { op: "sign_lease"; pem: string; kid: string; claims: Record<string, unknown> }
  | { op: "canonical_json"; value: Record<string, unknown> }
  | { op: "digest_key"; license_key: string; pepper: string }
  | { op: "device_id"; public_key_pem: string }
  | { op: "device_proof"; action: string; device_id: string; issued_at: number; nonce: string; app_version: string }
  | { op: "compare_semver"; left: string; right: string }
  | { op: "effective_features"; features: string[] }
  | { op: "issue_grant"; secret: string; artifact_id: string; device_id: string; ttl: number }
  | { op: "verify_grant"; secret: string; token: string; artifact_id: string }
  | { op: "grant_digest"; token: string };

function readStdin(): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    process.stdin.on("data", (chunk) => chunks.push(chunk as Buffer));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });
}

const operations: Op[] = JSON.parse(await readStdin());
const results: unknown[] = [];

for (const item of operations) {
  switch (item.op) {
    case "sign_lease": {
      const key = await importLeasePrivateKey(item.pem);
      results.push(await signLease(key, item.kid, item.claims as never));
      break;
    }
    case "canonical_json": {
      results.push(Buffer.from(canonicalJson(item.value)).toString("base64"));
      break;
    }
    case "digest_key": {
      results.push(await digestLicenseKey(item.license_key, item.pepper));
      break;
    }
    case "device_id": {
      results.push(await expectedDeviceId(await importDevicePublicKey(item.public_key_pem)));
      break;
    }
    case "device_proof": {
      results.push(
        Buffer.from(
          canonicalDeviceProof(
            item.action,
            item.device_id,
            item.issued_at,
            item.nonce,
            item.app_version,
          ),
        ).toString("base64"),
      );
      break;
    }
    case "compare_semver": {
      try {
        results.push(compareSemver(item.left, item.right));
      } catch (error) {
        results.push({ error: (error as Error).message });
      }
      break;
    }
    case "effective_features": {
      results.push(effectiveFeatures(item.features));
      break;
    }
    case "issue_grant": {
      const out = await issueDownloadGrant(item.secret, {
        artifactId: item.artifact_id,
        deviceId: item.device_id,
        ttlSeconds: item.ttl,
      });
      results.push({ token: out.token, expires_at: out.expiresAt });
      break;
    }
    case "verify_grant": {
      results.push(await verifyDownloadGrant(item.secret, item.token, item.artifact_id));
      break;
    }
    case "grant_digest": {
      results.push(await grantDigest(item.token));
      break;
    }
    default: {
      results.push({ error: `phep toan la: ${JSON.stringify(item)}` });
    }
  }
}

process.stdout.write(JSON.stringify(results));
