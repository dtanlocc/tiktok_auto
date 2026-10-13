// GET /functions/v1/artifacts/<artifact_id>/download
//
// Doi ve tai lay file backend. Ban Edge Function cua
// `control_plane/app/main.py:download_artifact`.
//
// ⛔ TEN VA HINH DUONG DAN BI RANG BUOC BOI MANIFEST DA KY. Launcher goi
// `GET manifest.download_url` voi `Authorization: Bearer <ve>` (lib.rs:915), va
// `download_url` nam TRONG phan da duoc ky nen khong sua duoc theo request.
// Vi the URL do phai co dinh: `.../functions/v1/artifacts/<id>/download`, va
// `scripts/supabase_release_admin.py` ky dung chuoi do. Doi ten function la
// moi manifest da ky trong DB thanh vo dung.
//
// ⛔ VA DO LA LY DO KHONG TRO THANG VAO URL CUA CHO CHUA. URL da ky het han
// sau vai phut; nhet no vao mot manifest ky mot lan la manifest chet theo.
// Function nay la lop co dinh o giua: kiem ve, roi ky mot URL ngan han NGAY
// LUC DO va chuyen huong sang do.
//
// ⛔ VA CHINH LOP DO CUU DUOC MOT LAN DOI CHO CHUA. Ban dau file nam tren
// Supabase Storage; Free plan chan upload o 50 MB va backend 0.1.12 la 50,7
// MiB - vuot dung 0,7 MiB va khong nang duoc tren plan mien phi. Chuyen sang
// Cloudflare R2 (10 GB, khong tinh egress, PUT toi 5 GB) chi phai sua function
// nay; KHONG phai ky lai mot manifest nao.

import { grantDigest, verifyDownloadGrant } from "../_shared/grant.ts";
import { amzDateFrom, presignR2Get } from "../_shared/r2.ts";
import {
  callRpc,
  clientBucket,
  jsonResponse,
  loadConfig,
  requireDelivery,
  verdictResponse,
} from "../_shared/service.ts";

/** `.../artifacts/<id>/download` -> `<id>`, hoac null. */
function artifactIdFromPath(pathname: string): string | null {
  const parts = pathname.replace(/\/+$/, "").split("/").filter(Boolean);
  if (parts.length < 3) return null;
  if (parts[parts.length - 1] !== "download") return null;
  const id = parts[parts.length - 2];
  if (parts[parts.length - 3] !== "artifacts") return null;
  // Id do minh sinh ra (`artifact_<32 hex>`); chan moi thu khac truoc khi no
  // di vao mot truy van hay mot duong dan Storage.
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$/.test(id)) return null;
  return id;
}

Deno.serve(async (request: Request): Promise<Response> => {
  // ⛔ GET, vi launcher dung `.get(url)`. Khong nhan POST o day de khong co hai
  // duong vao cho cung mot viec.
  if (request.method !== "GET") {
    return jsonResponse(405, { detail: "Method not allowed." });
  }

  const url = new URL(request.url);
  const artifactId = artifactIdFromPath(url.pathname);
  if (artifactId === null) {
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

  const authorization = request.headers.get("authorization") ?? "";
  const prefix = "Bearer ";
  const token = authorization.startsWith(prefix)
    ? authorization.slice(prefix.length)
    : "";
  const claims = await verifyDownloadGrant(
    config.downloadGrantSecret,
    token,
    artifactId,
  );
  if (claims === null) {
    return jsonResponse(401, { detail: "Download authorization is invalid." });
  }

  let verdict: Record<string, unknown>;
  try {
    verdict = await callRpc(config, "tkauto_claim_download", {
      p_artifact_id: artifactId,
      p_device_id: claims.device_id,
      p_grant_digest: await grantDigest(token),
      // Giu dau ve den qua han cua chinh no, de mot ve da dung khong the
      // duoc dung lai trong khoang con lai.
      p_grant_ttl: config.downloadGrantTtlSeconds * 2,
      p_rate_bucket: clientBucket(request, "device"),
      p_rate_limit: config.deviceRateLimitPerMinute,
      p_rate_window: 60,
    });
  } catch (error) {
    console.error("tkauto_claim_download that bai:", error);
    return jsonResponse(503, { detail: "License service is unavailable." });
  }
  if (verdict.ok !== true) {
    const code = String(verdict.code ?? "");
    if (code === "grant_already_used" || code === "artifact_unavailable") {
      // Ca hai deu la "ve nay khong dung duoc nua", va 401 la ma ban Python
      // dung cho ve khong hop le. Khong noi ro la da dung hay khong ton tai.
      return jsonResponse(401, { detail: "Download authorization is invalid." });
    }
    return verdictResponse(code);
  }

  // ⛔ KY URL NGAY LUC NAY, han bang han con lai cua ve. Bucket R2 la private,
  // nen day la duong duy nhat ra file - va no het han ngay sau do.
  //
  // ⛔ KY TAI CHO, KHONG GOI MANG. Khac ban Supabase Storage truoc day (phai
  // POST sang /storage/v1/object/sign roi doi tra loi), SigV4 la mot phep bam
  // thuan - khong co luot di mang nao, khong co gi de that bai vi mang, va
  // khong co do tre. Do la mot ly do nua R2 hon o cho nay.
  let signed: string;
  try {
    signed = await presignR2Get(
      {
        accountId: config.r2AccountId,
        bucket: config.r2Bucket,
        accessKeyId: config.r2AccessKeyId,
        secretAccessKey: config.r2SecretAccessKey,
      },
      String(verdict.storage_object),
      {
        expiresSeconds: config.downloadGrantTtlSeconds,
        amzDate: amzDateFrom(Math.floor(Date.now() / 1000)),
      },
    );
  } catch (error) {
    console.error("ky URL R2 that bai:", error);
    return jsonResponse(503, { detail: "Artifact storage is unavailable." });
  }

  // ⛔ 302, KHONG PHAI TU TRUYEN BYTE. File backend ~51 MB; day la Edge
  // Function, khong phai CDN - truyen qua day la tra gia CPU va cham tran
  // kich thuoc phan hoi. Signed URL di thang tu Storage.
  //
  // Launcher doc `content-length` cua phan hoi CUOI de doi chieu
  // `manifest.size_bytes`, nen chuyen huong khong lam mat phep kiem do; va
  // phep kiem that su quan trong - sha256 - launcher tu tinh tren byte da tai
  // roi so voi manifest DA KY. Nen cho nay khong can bam lai 41 MB moi lan
  // tai, khac ban Python (no bam lai vi file nam tren dia cua chinh no va re).
  return new Response(null, {
    status: 302,
    headers: {
      location: signed,
      "cache-control": "no-store",
      "referrer-policy": "no-referrer",
    },
  });
});
