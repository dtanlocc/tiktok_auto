"""group_id: cot tach du lieu cua DB dung chung

⛔ CHI LAM MOT VIEC. `--autogenerate` con de nghi them: ep NOT NULL cho
health_status, profile_status, country, batch_tag, created_at, note, va tao
index UNIQUE tren accounts.username. Tat ca deu la lech co san giua model va DB
(he di cu cu them cot bang `VARCHAR DEFAULT ''`, tuc nullable, trong khi model
khai `str`), khong phai thay doi cua lan nay.

Da do tren DB that ngay 06/10/2026: 0 username trung va 0 dong NULL o sau cot
kia, nen chung SE chay duoc o day. Nhung "o day" khong phai "o moi may khach":
mot DB co username trung thi `create_index(unique=True)` HONG, va vi init_db()
chay migration luc khoi dong, hong o day nghia la may do khong mo duoc app.
Mot migration mot viec thi khi no hong con biet vi sao.

Nen phan lech duoc de lai cho mot migration rieng, va migration do phai kem
buoc kiem/don du lieu truoc khi siet rang buoc.

Revision ID: 398e6eb7045c
Revises: 0001_baseline
"""
from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "398e6eb7045c"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

# ⛔ KHONG DUNG batch_alter_table. env.py bat render_as_batch cho SQLite vi no
# can thiet khi SUA cot - SQLite khong ALTER COLUMN duoc, batch phai dung lai ca
# bang roi copy du lieu. Nhung THEM cot va TAO index thi SQLite lam truc tiep
# duoc, nen op thuong tranh han mot lan dung lai bang 23 MB.
_TABLES = ("accounts", "proxies", "tiktok_video_metrics")


def upgrade() -> None:
    for table in _TABLES:
        # nullable=True, khong co server_default: NULL nghia la "ban cai rieng,
        # khong thuoc nhom nao" - dung voi LeaseClaims.group_id is None. Dat mot
        # default se bien moi dong cu thanh thanh vien cua mot nhom nao do.
        op.add_column(
            table,
            sa.Column("group_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        )
        op.create_index(f"ix_{table}_group_id", table, ["group_id"], unique=False)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_index(f"ix_{table}_group_id", table_name=table)
        op.drop_column(table, "group_id")
