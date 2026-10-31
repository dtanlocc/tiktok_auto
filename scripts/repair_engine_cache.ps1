<#
    Chua truong hop Firefox tai ve xong nhung khong dat vao cho duoc.

    ⛔ TRIEU CHUNG: app bao
        could not move the verified download into place: [WinError 5] Access is denied

    `os.replace` tren Windows KHONG ghi de duoc mot THU MUC dang ton tai, va
    truoc do thu vien goi `rmtree(..., ignore_errors=True)` - tuc neu co gi do
    dang giu thu muc dich thi loi bi nuot im lang, va buoc doi cho ra WinError 5.

    ⛔ PHAI CHAY TRUOC KHI MO LAI APP. Moi lan khoi dong, thu vien quet va XOA
    cac thu muc `.tmp-<tag>-<pid>` cua tien trinh da chet (`sweep_orphaned_tmp`).
    Ban tai 241 MB da tai xong nam trong mot thu muc nhu the, nen mo app truoc
    la mat no va phai tai lai tu dau.

    ⛔ KHONG GHI CUNG TEN BAN ENGINE. Ban dau script nay ghi thang
    "firefox-38_151.0_20261009061546"; chi mot lan nang thu vien la ten doi
    thanh "firefox-51_...", va script tro thanh vo dung dung luc can nhat.
    Gio no TU TIM: thu muc engine thieu firefox.exe la thu muc hong, thu muc
    .tmp co firefox.exe la ban da tai xong.
#>
param([string]$Cache = "$env:LOCALAPPDATA\invisible-playwright\invisible-playwright\Cache")

# ⛔ CHI GIET FIREFOX CUA TOOL. Loc theo duong dan, vi nguoi dung co the dang
# mo Firefox rieng cua ho - dong het la mat ca phien lam viec cua ho.
Get-Process firefox -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "*invisible-playwright*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

if (-not (Test-Path $Cache)) {
    Write-Host "Chua co thu muc engine - mo app len, no se tu tai."
    exit 0
}

$engine = Get-ChildItem $Cache -Directory -Filter "firefox-*" -ErrorAction SilentlyContinue
$hong   = $engine | Where-Object { -not (Test-Path (Join-Path $_.FullName "firefox.exe")) }
$lanh   = $engine | Where-Object {      Test-Path (Join-Path $_.FullName "firefox.exe")  }
$tam    = Get-ChildItem $Cache -Directory -Filter ".tmp-firefox-*" -Force -ErrorAction SilentlyContinue |
          Where-Object { Test-Path (Join-Path $_.FullName "firefox.exe") } |
          Select-Object -First 1

if (-not $hong) {
    Write-Host "Khong co thu muc engine hong ($($lanh.Count) ban day du) - khong can sua gi."
    Write-Host "Xong. Gio mo lai app."
    exit 0
}

foreach ($thu_muc in $hong) {
    $ten = $thu_muc.Name
    Remove-Item $thu_muc.FullName -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $thu_muc.FullName) {
        Write-Host "KHONG xoa duoc '$ten' - con chuong trinh dang giu no."
        Write-Host "Hay khoi dong lai may, roi chay lai dong lenh nay."
        exit 1
    }
    Write-Host "Da don thu muc hong: $ten"
    if ($tam) {
        Move-Item $tam.FullName (Join-Path $Cache $ten)
        Write-Host "Da dat ban tai xong vao dung cho - khong phai tai lai 241 MB."
        $tam = $null
    } else {
        Write-Host "Khong con ban tai tam - mo app len, no se tu tai lai (241 MB)."
    }
}
Write-Host "Xong. Gio mo lai app."
