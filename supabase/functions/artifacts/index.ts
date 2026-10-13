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
// ⛔ VA DO LA LY DO KHONG TRO THANG VAO SIGNED URL CUA STORAGE. Signed URL het
// han sau vai phut; nhet no vao mot manifest ky mot lan la manifest chet theo.
// Function nay la lop co dinh o giua: kiem ve, roi ky mot signed URL ngan han
// NGAY LUC DO va chuyen huong sang do.

import { grantDigest, verifyDownloadGrant } from "../_shared/grant.ts";
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

  // ⛔ KY SIGNED URL NGAY LUC NAY, han bang han con lai cua ve. Bucket la
  // private, nen day la duong duy nhat ra file - va no het han ngay sau do.
  let signed: string;
  try {
    const response = await fetch(
      `${config.supabaseUrl}/storage/v1/object/sign/${config.releaseBucket}/${verdict.storage_object}`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          apikey: config.serviceRoleKey,
          authorization: `Bearer ${config.serviceRoleKey}`,
        },
        body: JSON.stringify({ expiresIn: config.downloadGrantTtlSeconds }),
      },
    );
    if (!response.ok) {
      throw new Error(`${response.status} ${await response.text()}`);
    }
    const body = (await response.json()) as { signedURL?: string };
    if (!body.signedURL) throw new Error("Storage khong tra ve signedURL");
    // ⛔ NOI CHUOI, KHONG DUNG `new URL(path, base)`. Cach do am tham BO phan
    // duong dan cua base khi path bat dau bang "/": Storage tra ve
    // "/object/sign/<bucket>/<file>?token=..." nen
    // `new URL(that, "https://x/storage/v1")` ra "https://x/object/sign/..."
    // - mat han "/storage/v1", va 404. Bai test
    // `test_a_valid_grant_redirects_to_a_short_lived_storage_url` bat duoc
    // dung cho nay. Van nhan ca dang URL tuyet doi, vi mot so ban Storage tra
    // ve nhu vay.
    const raw = body.signedURL;
    signed = /^https?:\/\//i.test(raw)
      ? raw
      : `${config.supabaseUrl}/storage/v1/${raw.replace(/^\/+/, "")}`;
  } catch (error) {
    console.error("ky signed URL that bai:", error);
    return jsonResponse(503, { detail: "Artifact storage is unavailable." });
  }

  // ⛔ 302, KHONG PHAI TU TRUYEN BYTE. File backend ~41 MB; day la Edge
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
