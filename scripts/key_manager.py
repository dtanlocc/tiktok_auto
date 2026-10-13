"""Giao dien quan li key, chay TREN MAY OPERATOR.

⛔ TRANG NAY CHAY LOCAL, KHONG PHAI MOT TRANG TREN MANG, va do la mot quyet
dinh bao mat chu khong phai mot lua chon tien tay. Thao tac admin can MAT KHAU
DATABASE va PEPPER; mot trang hosted se phai giu hai thu do o dau do tren
Internet, hoac phai co mot endpoint admin cong khai - dung cai ma
docs/SUPABASE-KEY.md goi la "thu nguy hiem nhat co the dung". O day trinh duyet
chi noi voi 127.0.0.1, con bi mat khong roi may nay.

⛔ VA MOT CONG LOCAL KHONG PHAI LA AN TOAN MIEN PHI. Bat ky trang web nao dang
mo trong trinh duyet cua anh deu goi duoc 127.0.0.1 (CSRF), va mot ten mien tro
ve 127.0.0.1 con vuot duoc ca kiem tra same-origin (DNS rebinding). Nen:
  - mot token ngau nhien duoc sinh moi lan chay, va MOI request phai mang no;
  - header Host phai dung la 127.0.0.1 - chan duong DNS rebinding;
  - chi bind 127.0.0.1, khong bind 0.0.0.0.
Ba thu nay la ly do file nay dai hon mot CRUD thong thuong.

⛔ KEY THAT CHI HIEN MOT LAN, ngay sau khi tao, roi khong bao gio nua - vi
database chi giu digest HMAC. Trang se hien no mot lan kem nut copy, va ghi ra
file trong thu muc operator nhu CLI van lam.

Dung:
  uv run python scripts/key_manager.py --operator-directory D:/tkauto-operator-dev
"""
from __future__ import annotations

import argparse
import hmac
import json
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import supabase_license_admin as cli  # noqa: E402

# ⛔ IMPORT Ở TẦNG MODULE, KHÔNG TRONG HÀM, và đây là một lỗi đã xảy ra chứ
# không phải sở thích. `from __future__ import annotations` ở trên biến mọi
# annotation thành CHUỖI (PEP 563), và FastAPI phân giải chúng bằng
# `get_type_hints` theo **globals của module**. Khi `Request` được import bên
# trong `_build_app`, FastAPI không tìm thấy tên đó, không nhận ra đây là đối
# tượng request, và coi nó là một **query parameter bắt buộc** — mọi route trả
# về 422 `{"loc":["query","request"],"msg":"Field required"}`, một thông báo
# không nói gì về nguyên nhân thật.
from fastapi import FastAPI, Header, HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from app.security.license import group_feature, role_feature  # noqa: E402
from control_plane.app.models import new_id  # noqa: E402
from control_plane.app.security import (  # noqa: E402
    digest_license_key,
    generate_license_key,
)

_PAGE = Path(__file__).resolve().parent / "key_manager.html"


#: ⛔ HAI MODEL NAY PHAI O TANG MODULE, cung ly do voi cac import FastAPI o
#: tren: `from __future__ import annotations` lam annotation thanh chuoi, va
#: FastAPI phan giai chung theo globals cua module. Khai trong `_build_app`
#: thi `body: CreateBody` khong phan giai duoc va FastAPI coi `body` la mot
#: QUERY PARAMETER bat buoc - moi POST tra 422
#: `{"loc":["query","body"],"msg":"Field required"}`.
class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_reference: str = Field(default="", max_length=128)
    plan: str = Field(default="nhom", min_length=2, max_length=64)
    group: str = Field(default="", max_length=48)
    role: str = Field(default="", max_length=48)
    features: list[str] = Field(default_factory=list)
    max_accounts: int = Field(default=100, ge=1, le=1_000_000)
    max_tabs: int = Field(default=4, ge=1, le=1_000)
    max_devices: int = Field(default=1, ge=1, le=1_000)
    channel: str = Field(default="stable", pattern="^(internal|beta|stable)$")
    expires_days: int = Field(default=30, ge=1, le=3_650)

class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)


def _build_app(operator: Path, token: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def _guard(request: Request, supplied: str) -> None:
        # ⛔ Host PHAI la 127.0.0.1. Mot ten mien do ke khac so huu, tro ve
        # 127.0.0.1, se lam trinh duyet coi trang do la same-origin voi cong
        # nay (DNS rebinding) - va luc do token trong trang cua minh khong giup
        # gi, nhung kiem Host thi chan duoc.
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in {"127.0.0.1", "localhost"}:
            raise HTTPException(status_code=403, detail="Host khong duoc phep.")
        if not hmac.compare_digest(supplied or "", token):
            raise HTTPException(status_code=401, detail="Token khong dung.")

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # ⛔ Trang nay khong duoc nhung vao bat ky trang nao khac.
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/", response_class=HTMLResponse)
    def page(request: Request, t: str = ""):
        _guard(request, t)
        return HTMLResponse(_PAGE.read_text(encoding="utf-8"))

    def _rows(sql: str, params: tuple = ()) -> list[dict]:
        with cli._connect(operator) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                names = [d.name for d in cursor.description]
                return [dict(zip(names, row)) for row in cursor.fetchall()]

    @app.get("/api/licenses")
    def licenses(request: Request, x_tkauto_token: str = Header(default="")):
        _guard(request, x_tkauto_token)
        rows = _rows(
            """
            select id, customer_reference, plan, channel, group_feature,
                   role_feature, features, max_accounts, max_tabs, max_devices,
                   active_devices, expires_at, round(days_left)::int as days_left,
                   revoked, revoked_reason
              from license.overview
             order by revoked, days_left
            """
        )
        for row in rows:
            row["features"] = row["features"] or []
        return rows

    @app.get("/api/devices")
    def devices(request: Request, x_tkauto_token: str = Header(default="")):
        _guard(request, x_tkauto_token)
        return _rows(
            """
            select id, license_id, revoked_at,
                   to_char(to_timestamp(first_activated_at), 'DD/MM/YYYY HH24:MI') as first_at,
                   to_char(to_timestamp(last_seen_at), 'DD/MM/YYYY HH24:MI') as last_at
              from license.devices order by last_seen_at desc
            """
        )

    @app.get("/api/releases")
    def releases(request: Request, x_tkauto_token: str = Header(default="")):
        _guard(request, x_tkauto_token)
        return _rows(
            """
            select id, component, version, channel, active, mandatory,
                   round(size_bytes / 1048576.0, 1)::float as size_mb,
                   to_char(to_timestamp(published_at), 'DD/MM/YYYY HH24:MI') as published
              from license.releases order by published_at desc
            """
        )

    @app.post("/api/licenses")
    def create(
        body: CreateBody, request: Request, x_tkauto_token: str = Header(default="")
    ):
        _guard(request, x_tkauto_token)
        pepper = cli._read_secret(operator, cli._PEPPER_FILE, minimum=32)

        features = [item.strip() for item in body.features if item.strip()]
        if body.group.strip():
            features.append(group_feature(body.group.strip()))
        if body.role.strip():
            features.append(role_feature(body.role.strip()))
        features = list(dict.fromkeys(features))

        license_id = new_id("lic")
        license_key = generate_license_key()
        expires_at = int(time.time()) + body.expires_days * 86_400

        with cli._connect(operator) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    insert into license.licenses (
                        id, key_digest, customer_reference, plan, features,
                        max_accounts, max_tabs, max_devices, channel, expires_at
                    ) values (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
                    """,
                    (
                        license_id,
                        digest_license_key(license_key, pepper),
                        body.customer_reference,
                        body.plan,
                        json.dumps(features),
                        body.max_accounts,
                        body.max_tabs,
                        body.max_devices,
                        body.channel,
                        expires_at,
                    ),
                )
                cursor.execute(
                    """
                    insert into license.audit_events (event_type, actor_id, subject_id, metadata)
                    values ('license.created', 'key-manager', %s, %s::jsonb)
                    """,
                    (license_id, json.dumps({"customer": body.customer_reference})),
                )
            connection.commit()

        # ⛔ GHI RA FILE NHU CLI VAN LAM. Trang se hien key mot lan, nhung mot
        # tab bi dong truoc khi kip copy khong duoc phep lam mat key - va
        # database chi co digest nen khong co duong nao lay lai.
        safe = "".join(
            c if c.isalnum() or c in "-_" else "-" for c in body.customer_reference
        )
        out = operator / f"license-{safe or 'khach'}-{license_id[-8:]}.json"
        out.write_text(
            json.dumps(
                {
                    "license_id": license_id,
                    "license_key": license_key,
                    "customer_reference": body.customer_reference,
                    "plan": body.plan,
                    "features": features,
                    "max_accounts": body.max_accounts,
                    "max_tabs": body.max_tabs,
                    "max_devices": body.max_devices,
                    "channel": body.channel,
                    "expires_at": expires_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "license_id": license_id,
            "license_key": license_key,
            "features": features,
            "expires_at": expires_at,
            "saved_to": str(out),
        }

    @app.post("/api/licenses/{license_id}/revoke")
    def revoke(
        license_id: str,
        body: ReasonBody,
        request: Request,
        x_tkauto_token: str = Header(default=""),
    ):
        _guard(request, x_tkauto_token)
        with cli._connect(operator) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    update license.licenses
                       set revoked_at = extract(epoch from now())::bigint,
                           revoked_reason = %s
                     where id = %s and revoked_at is null
                    """,
                    (body.reason, license_id),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(
                        status_code=404, detail="Khong co license dang hieu luc voi id do."
                    )
                cursor.execute(
                    """
                    insert into license.audit_events (event_type, actor_id, subject_id, metadata)
                    values ('license.revoked', 'key-manager', %s, %s::jsonb)
                    """,
                    (license_id, json.dumps({"reason": body.reason})),
                )
            connection.commit()
        # Noi ro do tre, de khong ai tuong thu hoi la tuc thi.
        # ⛔ Noi dung do tre. Mot 403 o /lease/renew lam launcher dung backend
        # va xoa .exe ngay; giao dien goi renew moi 15 phut va mot lan luc mo
        # app. Chi khi may OFFLINE moi phai doi lease het han.
        return {
            "ok": True,
            "note": "Co hieu luc o dot gia han ke tiep: toi 15 phut neu app "
            "dang mo va co mang, ngay lap tuc neu mo lai app, hoac khi lease "
            "het han (toi 1 gio) + 60 giay neu may offline. Luc do backend bi "
            "dung va file .exe bi xoa khoi may khach.",
        }

    @app.post("/api/devices/{device_id}/revoke")
    def revoke_device(
        device_id: str,
        body: ReasonBody,
        request: Request,
        x_tkauto_token: str = Header(default=""),
    ):
        _guard(request, x_tkauto_token)
        with cli._connect(operator) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    update license.devices
                       set revoked_at = extract(epoch from now())::bigint
                     where id = %s and revoked_at is null
                    """,
                    (device_id,),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(
                        status_code=404, detail="Khong co thiet bi dang hieu luc voi id do."
                    )
                cursor.execute(
                    """
                    insert into license.audit_events (event_type, actor_id, subject_id, metadata)
                    values ('device.revoked', 'key-manager', %s, %s::jsonb)
                    """,
                    (device_id, json.dumps({"reason": body.reason})),
                )
            connection.commit()
        return {"ok": True, "note": "O may do duoc tra lai cho license."}

    @app.post("/api/releases/{artifact_id}/deactivate")
    def deactivate(
        artifact_id: str, request: Request, x_tkauto_token: str = Header(default="")
    ):
        _guard(request, x_tkauto_token)
        with cli._connect(operator) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "update license.releases set active = false where id = %s and active",
                    (artifact_id,),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(
                        status_code=404, detail="Khong co ban phat hanh dang bat voi id do."
                    )
                cursor.execute(
                    """
                    insert into license.audit_events (event_type, actor_id, subject_id)
                    values ('release.deactivated', 'key-manager', %s)
                    """,
                    (artifact_id,),
                )
            connection.commit()
        return {"ok": True}

    @app.exception_handler(HTTPException)
    def _json_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-directory", required=True, type=Path)
    parser.add_argument("--port", type=int, default=0,
                        help="0 = cho he dieu hanh chon mot cong trong.")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    operator = args.operator_directory.expanduser().resolve()
    if not operator.is_dir():
        raise SystemExit(f"Khong thay thu muc operator: {operator}")
    if not _PAGE.is_file():
        raise SystemExit(f"Thieu {_PAGE.name} canh script.")
    # Doc truoc de bao loi ngay neu thieu bi mat, thay vi bao khi bam nut.
    cli._read_secret(operator, cli._PEPPER_FILE, minimum=32)
    cli._read_secret(operator, cli._DATABASE_URL_FILE, minimum=20)

    import socket

    import uvicorn

    port = args.port
    if port == 0:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

    token = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{port}/?t={token}"
    app = _build_app(operator, token)

    # ⛔ FLUSH, khong de Python tu quyet dinh. Khi stdout khong phai terminal
    # (chay qua mot script, hay redirect ra file) Python chuyen sang buffer theo
    # khoi, va uvicorn thi chay mai khong thoat - nen URL kem token NAM TRONG
    # BUFFER vo thoi han. Operator thay mot cua so trong va khong co cach nao
    # vao duoc trang. Da gap dung the.
    for line in (
        "",
        "Quan li key - mo duong nay trong trinh duyet:",
        f"  {url}",
        "",
        "Token doi moi lan chay. Dong cua so nay la tat server.",
    ):
        print(line, flush=True)

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # ⛔ CHI 127.0.0.1. Bind 0.0.0.0 la mo cong quan li key ra ca mang LAN.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
