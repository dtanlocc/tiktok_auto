import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from app.domain.ports.repository import IProxyRepository
from app.domain.entities.proxy import Proxy
from app.interfaces.api.deps import get_proxy_repository
from app.interfaces.dto.proxy_dto import ProxyOut

router = APIRouter(prefix="/proxies", tags=["Proxies"])

@router.get("/", response_model=List[ProxyOut])
async def list_proxies(
    proxy_repo: IProxyRepository = Depends(get_proxy_repository)
):
    proxies = proxy_repo.get_all()
    return [
        ProxyOut(
            id=p.id,
            host=p.host,
            port=p.port,
            username=p.username,
            protocol=p.protocol
        )
        for p in proxies
    ]

@router.post("/import-file", status_code=status.HTTP_201_CREATED)
async def import_proxies_from_files(
    files: List[UploadFile] = File(...),  # <-- NÂNG CẤP: Nhận danh sách nhiều file cùng lúc
    proxy_repo: IProxyRepository = Depends(get_proxy_repository)
):
    """API Nhập hàng loạt Proxy bằng cách tải lên nhiều file .txt cùng lúc"""
    try:
        imported_count = 0
        for file in files:
            content = await file.read()
            lines = content.decode("utf-8").splitlines()

            for line in lines:
                if not line.strip():
                    continue
                
                parts = line.strip().split("|")
                if len(parts) >= 3:
                    host = parts[0].strip()
                    port = int(parts[1].strip())
                    protocol = parts[2].strip()
                    username = parts[3].strip() if len(parts) > 3 else None
                    password = parts[4].strip() if len(parts) > 4 else None
                else:
                    raw_line = line.strip()
                    try:
                        protocol, rest = raw_line.split("://")
                        if "@" in rest:
                            creds, address = rest.split("@")
                            username, password = creds.split(":")
                            host, port = address.split(":")
                        else:
                            username, password = None, None
                            host, port = rest.split(":")
                        port = int(port)
                    except Exception:
                        continue

                new_proxy = Proxy(
                    id=str(uuid.uuid4()),
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    protocol=protocol
                )
                proxy_repo.save(new_proxy)
                imported_count += 1

        return {"status": "SUCCESS", "message": f"Đã nhập thành công {imported_count} Proxy từ {len(files)} tệp tin."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Không thể xử lý tệp Proxy: {str(e)}")