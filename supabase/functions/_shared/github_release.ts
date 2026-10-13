// Lay duong tai mot release asset tren repo PRIVATE cua GitHub.
//
// ⛔ VI SAO LA GITHUB CHU KHONG PHAI R2 HAY SUPABASE STORAGE, theo dung thu tu
// da thu va bi loai:
//   1. Supabase Storage - Free plan chan upload o 50 MB va KHONG nang duoc tu
//      dashboard; backend 0.1.12 la 50,7 MiB, vuot dung 0,7 MiB.
//   2. Cloudflare R2 - mien phi 10 GB va khong tinh egress, nhung BAT THEM
//      PHUONG THUC THANH TOAN de bat lan dau. Chu du an khong dua the.
//   3. GitHub Release tren mot repo private - asset toi 2 GB, khong the, va
//      tai khoan GitHub thi da co.
//
// ⛔ REPO PHAI LA PRIVATE. Asset cua release tren repo public tai duoc khong
// can xac thuc gi, va luc do ca chuoi ve tai + kiem thiet bi + kiem license
// thanh trang tri. Repo `tiktok_auto` hien la PUBLIC, nen artifact KHONG duoc
// nam o do.
//
// ⛔ VA TOKEN KHONG BAO GIO RA KHOI FUNCTION. GitHub tra ve 302 sang mot URL
// da mang chu ky trong query (`release-assets.githubusercontent.com`), va URL
// do khong can xac thuc. Function doc Location roi chuyen huong client sang
// do; client khong he thay token.

export interface GithubReleaseConfig {
  /** Dang `owner/repo`. */
  repo: string;
  token: string;
}

export interface AssetLocation {
  /** URL ngan han, khong can xac thuc. */
  url: string | null;
  /**
   * Khi GitHub tu truyen byte thay vi chuyen huong. Tai lieu noi ro client
   * phai xu ly duoc CA HAI ("either redirect the client to the location, or
   * stream it directly if possible"), nen khong duoc gia dinh chi co 302.
   */
  streamed: Response | null;
}

export function isValidRepo(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(value);
}

/**
 * Doi mot asset id thanh duong tai.
 *
 * ⛔ `redirect: "manual"` LA BAT BUOC O DAY. Voi mac dinh ("follow") thi fetch
 * tu di theo va tra ve 200 kem 51 MB than request - tuc Edge Function phai tu
 * truyen het so byte do: ton bang thong cua project, va cham tran 150 giay
 * wall-clock neu may khach tai cham (51 MB o 300 KB/s la ~170 giay, tuc that
 * bai giua duong). Doc Location roi chuyen huong thi byte di thang tu CDN cua
 * GitHub ve may khach.
 *
 * Do ngay 13/10/2026 bang Node 24 tren mot asset public: `redirect: "manual"`
 * tra status=302 va Location DOC DUOC; `redirect: "follow"` tra 200 kem noi
 * dung. Va URL da ky do chay ca khi co lan khong co header Authorization.
 */
export async function resolveAssetLocation(
  config: GithubReleaseConfig,
  assetId: string,
): Promise<AssetLocation> {
  if (!/^[0-9]{1,20}$/.test(assetId)) {
    throw new Error(`asset id khong hop le: ${assetId}`);
  }
  const response = await fetch(
    `https://api.github.com/repos/${config.repo}/releases/assets/${assetId}`,
    {
      headers: {
        accept: "application/octet-stream",
        authorization: `Bearer ${config.token}`,
        "x-github-api-version": "2022-11-28",
        // GitHub tu choi request khong co User-Agent.
        "user-agent": "tkauto-artifacts",
      },
      redirect: "manual",
    },
  );

  if (response.status >= 300 && response.status < 400) {
    const location = response.headers.get("location");
    // Than cua phan hoi 302 la rong, nhung van phai huy de khong giu ket noi.
    try {
      await response.body?.cancel();
    } catch {
      // khong sao
    }
    if (!location) throw new Error("GitHub chuyen huong nhung khong co Location");
    const target = new URL(location);
    if (target.protocol !== "https:") {
      throw new Error(`Location khong phai HTTPS: ${target.protocol}`);
    }
    return { url: target.toString(), streamed: null };
  }

  if (response.ok) {
    return { url: null, streamed: response };
  }

  // 404 o day thuong la asset da bi xoa tay tren GitHub, hoac token khong co
  // quyen doc repo - hai thu rat khac nhau nhung GitHub tra cung mot ma cho
  // repo private de khong tiet lo su ton tai cua repo.
  throw new Error(
    `GitHub tra ve ${response.status} cho asset ${assetId}: ` +
      (await response.text()).slice(0, 300),
  );
}
