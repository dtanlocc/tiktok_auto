import uuid
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from app.domain.ports.repository import IProxyRepository
from app.domain.entities.proxy import Proxy
from app.interfaces.api.deps import get_proxy_repository, require_runtime_entitlement
from app.interfaces.dto.proxy_dto import (
    SUPPORTED_PROXY_PROTOCOLS,
    ProxyCheckIn,
    ProxyCreateIn,
    ProxyImportTextIn,
    ProxyOut,
    ProxyUpdateIn,
)
from app.infrastructure.websocket.socket_manager import ws_manager
from app.use_cases.proxies.proxy_health_check import check_proxies

router = APIRouter(
    prefix="/proxies",
    tags=["Proxies"],
    dependencies=[Depends(require_runtime_entitlement("app.start"))],
)


ProxyParts = Tuple[str, int, str, Optional[str], Optional[str]]


def _parse_proxy_line(line: str) -> Optional[ProxyParts]:
    """Parse one proxy line while keeping the legacy import formats working."""
    raw_line = line.strip()
    if not raw_line:
        return None

    try:
        if "|" in raw_line:
            parts = [part.strip() for part in raw_line.split("|")]
            if len(parts) < 3:
                return None

            host, port_text, protocol = parts[:3]
            username = (parts[3] or None) if len(parts) > 3 else None
            password = (parts[4] or None) if len(parts) > 4 else None
        else:
            protocol, separator, address_and_credentials = raw_line.partition("://")
            if not separator:
                return None

            protocol = protocol.strip()
            if "@" in address_and_credentials:
                credentials, address = address_and_credentials.rsplit("@", 1)
                username, password = credentials.split(":", 1)
                host, port_text = address.rsplit(":", 1)
            else:
                # Supported URL-like formats:
                #   protocol://host:port
                #   protocol://host:port:username:password
                parts = address_and_credentials.split(":", 3)
                if len(parts) == 2:
                    host, port_text = parts
                    username, password = None, None
                elif len(parts) == 4:
                    host, port_text, username, password = parts
                else:
                    return None

            host = host.strip()
            port_text = port_text.strip()
            username = username.strip() or None if username is not None else None
            password = password.strip() or None if password is not None else None

        port = int(port_text)
        if not host or not protocol or not 1 <= port <= 65535:
            return None

        return host, port, protocol, username, password
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalized_fields(
    host: str, port: int, protocol: str, username: Optional[str]
) -> Tuple[str, int, str, Optional[str]]:
    """Validated route fields, or HTTP 400 naming what is wrong."""
    host = (host or "").strip()
    protocol = (protocol or "").strip().lower()
    username = (username or "").strip() or None
    if not host or any(ch in host for ch in " /@"):
        raise HTTPException(status_code=400, detail="Host proxy không hợp lệ.")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="Port phải từ 1 đến 65535.")
    if protocol not in SUPPORTED_PROXY_PROTOCOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Giao thức phải là một trong: {', '.join(SUPPORTED_PROXY_PROTOCOLS)}.",
        )
    return host, port, protocol, username


def _to_out(proxy: Proxy, counts: Dict[str, int]) -> ProxyOut:
    return ProxyOut(
        id=str(proxy.id),
        host=proxy.host,
        port=proxy.port,
        username=proxy.username,
        protocol=proxy.protocol,
        has_password=bool(proxy.password),
        label=proxy.label or "",
        note=proxy.note or "",
        enabled=bool(proxy.enabled),
        created_at=proxy.created_at or "",
        check_status=proxy.check_status or "UNCHECKED",
        check_error=proxy.check_error or "",
        checked_at=proxy.checked_at or "",
        exit_ip=proxy.exit_ip or "",
        country=proxy.country or "",
        latency_ms=proxy.latency_ms,
        tiktok_ok=proxy.tiktok_ok,
        cdn_ok=proxy.cdn_ok,
        account_count=counts.get(str(proxy.id), 0),
    )


def _find_duplicate(
    proxies: Iterable[Proxy], candidate: Proxy, ignore_id: Optional[str] = None
) -> Optional[Proxy]:
    key = candidate.endpoint_key
    return next(
        (p for p in proxies if p.endpoint_key == key and str(p.id) != str(ignore_id)),
        None,
    )


async def _announce_change() -> None:
    await ws_manager.broadcast({"event": "PROXIES_CHANGED", "data": {}})


@router.get("/", response_model=List[ProxyOut])
async def list_proxies(proxy_repo: IProxyRepository = Depends(get_proxy_repository)):
    counts = proxy_repo.account_counts()
    proxies = sorted(
        proxy_repo.get_all(),
        key=lambda p: ((p.label or "").casefold(), p.host.casefold(), p.port),
    )
    return [_to_out(p, counts) for p in proxies]


@router.post("/", response_model=ProxyOut, status_code=status.HTTP_201_CREATED)
async def create_proxy(
    payload: ProxyCreateIn,
    proxy_repo: IProxyRepository = Depends(get_proxy_repository),
):
    host, port, protocol, username = _normalized_fields(
        payload.host, payload.port, payload.protocol, payload.username
    )
    proxy = Proxy(
        id=str(uuid.uuid4()),
        host=host,
        port=port,
        protocol=protocol,
        username=username,
        password=(payload.password or None) if username else None,
        label=payload.label.strip(),
        note=payload.note.strip(),
        enabled=payload.enabled,
        created_at=_now(),
    )
    duplicate = _find_duplicate(proxy_repo.get_all(), proxy)
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"Proxy {protocol}://{host}:{port} đã có trong kho"
            + (f" ({duplicate.label})." if duplicate.label else "."),
        )
    saved = proxy_repo.save(proxy)
    await _announce_change()
    return _to_out(saved, {})


@router.patch("/{proxy_id}", response_model=ProxyOut)
async def update_proxy(
    proxy_id: str,
    payload: ProxyUpdateIn,
    proxy_repo: IProxyRepository = Depends(get_proxy_repository),
):
    proxy = proxy_repo.get_by_id(proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail="Không tìm thấy proxy.")
    changes = payload.model_dump(exclude_unset=True)
    route_changed = any(
        name in changes for name in ("host", "port", "protocol", "username", "password")
    )
    host, port, protocol, username = _normalized_fields(
        changes.get("host", proxy.host),
        changes.get("port", proxy.port),
        changes.get("protocol", proxy.protocol),
        changes.get("username", proxy.username),
    )
    proxy.host, proxy.port, proxy.protocol, proxy.username = host, port, protocol, username
    if "password" in changes and changes["password"] is not None:
        proxy.password = changes["password"] or None
    if not proxy.username:
        proxy.password = None
    for name in ("label", "note"):
        if changes.get(name) is not None:
            setattr(proxy, name, str(changes[name]).strip())
    if changes.get("enabled") is not None:
        proxy.enabled = bool(changes["enabled"])

    duplicate = _find_duplicate(proxy_repo.get_all(), proxy, ignore_id=proxy_id)
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"Proxy {protocol}://{host}:{port} đã có trong kho"
            + (f" ({duplicate.label})." if duplicate.label else "."),
        )
    if route_changed:
        # The last check described the old route.
        proxy.check_status, proxy.check_error, proxy.checked_at = "UNCHECKED", "", ""
        proxy.exit_ip, proxy.country, proxy.latency_ms = "", "", None
        proxy.tiktok_ok, proxy.cdn_ok = None, None
    saved = proxy_repo.save(proxy)
    await _announce_change()
    return _to_out(saved, proxy_repo.account_counts())


@router.delete("/{proxy_id}")
async def delete_proxy(
    proxy_id: str,
    detach_accounts: bool = False,
    proxy_repo: IProxyRepository = Depends(get_proxy_repository),
):
    """Xóa proxy. detach_accounts=true: gỡ proxy khỏi các account đang gán
    (chúng thành Mạng thật - không proxy) rồi mới xóa."""
    proxy = proxy_repo.get_by_id(proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail="Không tìm thấy proxy.")
    in_use = proxy_repo.account_counts().get(str(proxy_id), 0)
    detached: list[str] = []
    if in_use and not detach_accounts:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Proxy đang gán cho {in_use} account. Hãy phân bổ các account đó sang "
                "proxy khác, hoặc gỡ proxy (chạy Mạng thật), trước khi xóa."
            ),
        )
    if in_use:
        detached = proxy_repo.detach_accounts(proxy_id)
    proxy_repo.delete(proxy_id)
    for account_id in detached:
        await ws_manager.broadcast(
            {"event": "ACCOUNT_PROXY_CHANGED", "data": {"id": account_id, "proxy_id": None}}
        )
    await _announce_change()
    message = f"Đã xóa proxy {proxy.host}:{proxy.port}."
    if detached:
        message = (
            f"Đã gỡ proxy khỏi {len(detached)} account (thành Mạng thật - không proxy) "
            f"và xóa proxy {proxy.host}:{proxy.port}."
        )
    return {"status": "SUCCESS", "detached": len(detached), "message": message}


def _import_lines(lines: Iterable[str], proxy_repo: IProxyRepository) -> Dict[str, int]:
    existing = list(proxy_repo.get_all())
    imported = duplicates = invalid = 0
    for line in lines:
        if not line.strip() or line.strip().startswith("#"):
            continue
        parsed = _parse_proxy_line(line)
        if parsed is None:
            invalid += 1
            continue
        host, port, protocol, username, password = parsed
        protocol = protocol.strip().lower()
        if protocol not in SUPPORTED_PROXY_PROTOCOLS:
            invalid += 1
            continue
        proxy = Proxy(
            id=str(uuid.uuid4()),
            host=host,
            port=port,
            username=username,
            password=password,
            protocol=protocol,
            created_at=_now(),
        )
        if _find_duplicate(existing, proxy):
            duplicates += 1
            continue
        proxy_repo.save(proxy)
        existing.append(proxy)
        imported += 1
    return {"imported": imported, "duplicates": duplicates, "invalid": invalid}


def _import_message(result: Dict[str, int], source: str) -> str:
    message = f"Đã nhập {result['imported']} Proxy từ {source}"
    extras = []
    if result["duplicates"]:
        extras.append(f"bỏ qua {result['duplicates']} proxy đã có")
    if result["invalid"]:
        extras.append(f"{result['invalid']} dòng sai định dạng")
    return message + (f" ({'; '.join(extras)})." if extras else ".")


@router.post("/import-file", status_code=status.HTTP_201_CREATED)
async def import_proxies_from_files(
    files: List[UploadFile] = File(...),
    proxy_repo: IProxyRepository = Depends(get_proxy_repository),
):
    """API Nhập hàng loạt Proxy bằng cách tải lên nhiều file .txt cùng lúc"""
    try:
        lines: List[str] = []
        for file in files:
            content = await file.read()
            lines.extend(content.decode("utf-8-sig").splitlines())
        result = _import_lines(lines, proxy_repo)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Không thể xử lý tệp Proxy: {str(e)}"
        )
    if result["imported"]:
        await _announce_change()
    return {
        "status": "SUCCESS",
        **result,
        "message": _import_message(result, f"{len(files)} tệp tin"),
    }


@router.post("/import-text", status_code=status.HTTP_201_CREATED)
async def import_proxies_from_text(
    payload: ProxyImportTextIn,
    proxy_repo: IProxyRepository = Depends(get_proxy_repository),
):
    result = _import_lines(payload.text.splitlines(), proxy_repo)
    if result["imported"]:
        await _announce_change()
    return {"status": "SUCCESS", **result, "message": _import_message(result, "văn bản dán vào")}


@router.post("/check", response_model=List[ProxyOut])
async def check_proxy_health(
    payload: ProxyCheckIn,
    proxy_repo: IProxyRepository = Depends(get_proxy_repository),
):
    """Kiểm tra qua từng proxy: IP ra, quốc gia, độ trễ, vào được TikTok và CDN của TikTok."""
    proxies = proxy_repo.get_all()
    if payload.proxy_ids is not None:
        wanted = {str(proxy_id) for proxy_id in payload.proxy_ids}
        proxies = [p for p in proxies if str(p.id) in wanted]
    if not proxies:
        raise HTTPException(status_code=400, detail="Không có proxy nào để kiểm tra.")
    results = await check_proxies(proxies)
    checked = []
    for proxy in proxies:
        # Re-read: the proxy may have been edited while its check ran.
        current = proxy_repo.get_by_id(str(proxy.id))
        if current is None or current.endpoint_key != proxy.endpoint_key:
            continue
        checked.append(proxy_repo.save(results[str(proxy.id)].apply_to(current)))
    await _announce_change()
    counts = proxy_repo.account_counts()
    return [_to_out(p, counts) for p in checked]
