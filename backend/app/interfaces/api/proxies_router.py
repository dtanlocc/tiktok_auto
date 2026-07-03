import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from app.domain.ports.repository import IProxyRepository
from app.domain.entities.proxy import Proxy
from app.interfaces.api.deps import get_proxy_repository
from app.interfaces.dto.proxy_dto import ProxyCreateIn, ProxyOut

router = APIRouter(prefix="/proxies", tags=["Proxies"])

@router.post("/", response_model=ProxyOut, status_code=status.HTTP_201_CREATED)
async def create_proxy(
    payload: ProxyCreateIn,
    proxy_repo: IProxyRepository = Depends(get_proxy_repository)
):
    new_proxy = Proxy(
        id=str(uuid.uuid4()),
        host=payload.host,
        port=payload.port,
        username=payload.username,
        password=payload.password,
        protocol=payload.protocol
    )
    try:
        saved = proxy_repo.save(new_proxy)
        return ProxyOut(
            id=saved.id,
            host=saved.host,
            port=saved.port,
            username=saved.username,
            protocol=saved.protocol
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Không thể lưu Proxy: {str(e)}"
        )

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