import uuid
from typing import List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from app.domain.ports.repository import IProxyRepository
from app.domain.entities.proxy import Proxy
from app.interfaces.api.deps import get_proxy_repository, require_runtime_entitlement
from app.interfaces.dto.proxy_dto import ProxyOut

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


@router.get("/", response_model=List[ProxyOut])
async def list_proxies(proxy_repo: IProxyRepository = Depends(get_proxy_repository)):
    proxies = proxy_repo.get_all()
    return [
        ProxyOut(
            id=p.id, host=p.host, port=p.port, username=p.username, protocol=p.protocol
        )
        for p in proxies
    ]


@router.post("/import-file", status_code=status.HTTP_201_CREATED)
async def import_proxies_from_files(
    files: List[UploadFile] = File(
        ...
    ),  # <-- NÂNG CẤP: Nhận danh sách nhiều file cùng lúc
    proxy_repo: IProxyRepository = Depends(get_proxy_repository),
):
    """API Nhập hàng loạt Proxy bằng cách tải lên nhiều file .txt cùng lúc"""
    try:
        imported_count = 0
        for file in files:
            content = await file.read()
            lines = content.decode("utf-8").splitlines()

            for line in lines:
                parsed_proxy = _parse_proxy_line(line)
                if parsed_proxy is None:
                    continue
                host, port, protocol, username, password = parsed_proxy

                new_proxy = Proxy(
                    id=str(uuid.uuid4()),
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    protocol=protocol,
                )
                proxy_repo.save(new_proxy)
                imported_count += 1

        return {
            "status": "SUCCESS",
            "message": f"Đã nhập thành công {imported_count} Proxy từ {len(files)} tệp tin.",
        }
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Không thể xử lý tệp Proxy: {str(e)}"
        )
