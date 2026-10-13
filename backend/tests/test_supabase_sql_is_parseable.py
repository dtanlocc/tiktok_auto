"""SQL cua Supabase phai qua duoc parser cua chinh PostgreSQL.

⛔ VI SAO CAN BAI TEST NAY. May phat trien khong co Postgres: Docker Desktop
co cai nhung service `com.docker.service` o trang thai Stopped va bat no can
quyen admin, nen khong dung duoc `supabase start`. Khong co cho nay thi duong
duy nhat phat hien mot dau phay thieu trong `supabase/migrations/*.sql` la
chay migration len du an THAT - tuc len database dang giu key cua khach.

`pglast` goi libpg_query, la parser cua chinh PostgreSQL, nen no doc file nay
dung nhu server se doc. Va `parse_plpgsql` doc duoc CA THAN HAM plpgsql - thu
ma khong linter SQL nao khac lam duoc, va cung la noi toan bo logic kich hoat
nam.

⛔ PARSE DUOC KHONG CO NGHIA LA CHAY DUNG. Bai test nay bat loi cu phap va chi
loi cu phap: no khong biet bang `license.licenses` co that khong, khong kiem
duoc kieu, khong chay duoc mot kich hoat nao. Phan hanh vi phai duoc kiem bang
mot kich hoat that tren du an Supabase - xem docs/SUPABASE-KEY.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pglast = pytest.importorskip(
    "pglast",
    reason=(
        "Thieu pglast nen khong kiem duoc SQL cua Supabase. Cai bang "
        "`uv pip install pglast` hoac `uv sync` (no nam trong nhom dev)."
    ),
)

_MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def _sql_files() -> list[Path]:
    return sorted(_MIGRATIONS.glob("*.sql"))


def _count_plpgsql_bodies(statements) -> int:
    """Dem than plpgsql TU AST, khong tu van ban.

    ⛔ Dem bang `text.count("language plpgsql")` la sai, va da sai mot lan:
    `parse_plpgsql` doc CA khoi `DO $$ ... $$` vo danh, ma khoi do khong he co
    chu "language plpgsql" trong no - nen file chi co mot khoi DO bi bao la
    "khai 0 than nhung doc duoc 1".
    """
    from pglast import ast

    total = 0
    for statement in statements:
        node = getattr(statement, "stmt", statement)
        if isinstance(node, ast.DoStmt):
            total += 1
            continue
        if isinstance(node, ast.CreateFunctionStmt):
            for option in node.options or ():
                if option.defname != "language":
                    continue
                if getattr(option.arg, "sval", None) == "plpgsql":
                    total += 1
                    break
    return total


def test_the_migration_directory_is_not_empty():
    """Chan truong hop bai test nay xanh vi khong tim thay file nao de doc."""
    assert _sql_files(), (
        f"Khong thay file .sql nao trong {_MIGRATIONS}. Neu thu muc migration "
        "doi cho thi phai sua duong dan o day, khong phai xoa bai test."
    )


@pytest.mark.parametrize(
    "path", _sql_files(), ids=lambda item: item.name
)
def test_every_statement_parses(path: Path):
    from pglast import parse_sql

    statements = parse_sql(path.read_text(encoding="utf-8"))
    assert statements, f"{path.name} khong co cau lenh nao."


@pytest.mark.parametrize(
    "path", _sql_files(), ids=lambda item: item.name
)
def test_every_plpgsql_body_parses(path: Path):
    """⛔ Day la phan quan trong: logic kich hoat nam trong than plpgsql.

    `parse_sql` o tren coi than ham la mot chuoi va khong doc vao trong, nen
    mot `end if;` thieu van qua duoc no. `parse_plpgsql` thi doc.
    """
    from pglast import parse_plpgsql, parse_sql

    text = path.read_text(encoding="utf-8")
    blocks = parse_plpgsql(text)
    expected = _count_plpgsql_bodies(parse_sql(text))
    assert len(blocks) == expected, (
        f"{path.name}: DDL khai {expected} than plpgsql nhung parser chi doc "
        f"duoc {len(blocks)}. Mot than bi bo qua la mot than KHONG DUOC KIEM - "
        "thuong vi mot bien duoc khai kieu co ten schema "
        "(`license.licenses`), thu ma libpg_query khong tra cuu duoc."
    )


def test_the_license_tables_stay_out_of_the_schema_postgrest_serves():
    """⛔ Bang key KHONG duoc nam o `public`.

    Anon key duoc nhung vao app khach nen coi nhu da lo. PostgREST chi phuc vu
    `public` (va graphql_public), nen mot bang key o `public` la mot endpoint
    HTTP cong khai cho bat ky ai co file .exe. Bai test nay chan viec mot
    `create table` sau nay vo tinh roi vao `public`.
    """
    from pglast import parse_sql
    from pglast.visitors import Visitor

    created: list[tuple[str | None, str]] = []

    class Collect(Visitor):
        def visit_CreateStmt(self, ancestors, node):  # noqa: N802, ARG002
            created.append((node.relation.schemaname, node.relation.relname))

    for path in _sql_files():
        for statement in parse_sql(path.read_text(encoding="utf-8")):
            Collect()(statement)

    assert created, "Khong thay `create table` nao - bai test dang khong doc gi."
    misplaced = [name for schema, name in created if schema != "license"]
    assert not misplaced, (
        "Nhung bang nay khong nam trong schema `license`: "
        f"{', '.join(misplaced)}. Chuyen chung vao `license.` truoc khi ap "
        "migration: o `public` la PostgREST phuc vu chung ra Internet."
    )
