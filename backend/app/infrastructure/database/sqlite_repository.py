# File: backend/app/infrastructure/database/sqlite_repository.py
import json
import uuid
from typing import List, Optional, Tuple
from sqlmodel import Session, select
from app.domain.entities.account import TikTokAccount
from app.domain.entities.proxy import Proxy
from app.domain.ports.repository import IAccountRepository, IProxyRepository
from app.infrastructure.database.schemas import AccountDbTable, ProxyDbTable

class SQLiteProxyRepository(IProxyRepository):
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, proxy_id: str) -> Optional[Proxy]:
        db_row = self.session.get(ProxyDbTable, proxy_id)
        if not db_row:
            return None
        return Proxy(
            id=db_row.id,
            host=db_row.host,
            port=db_row.port,
            username=db_row.username,
            password=db_row.password,
            protocol=db_row.protocol
        )

    def save(self, proxy: Proxy) -> Proxy:
        db_row = self.session.get(ProxyDbTable, proxy.id) if proxy.id else None
        if not db_row:
            db_row = ProxyDbTable(id=proxy.id, host=proxy.host, port=proxy.port)
        
        db_row.username = proxy.username
        db_row.password = proxy.password
        db_row.protocol = proxy.protocol

        self.session.add(db_row)
        self.session.commit()
        self.session.refresh(db_row)
        
        proxy.id = db_row.id
        return proxy

    def get_all(self) -> List[Proxy]:
        statement = select(ProxyDbTable)
        results = self.session.exec(statement).all()
        return [
            Proxy(
                id=row.id,
                host=row.host,
                port=row.port,
                username=row.username,
                password=row.password,
                protocol=row.protocol
            )
            for row in results
        ]


class SQLiteAccountRepository(IAccountRepository):
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, account_id: str) -> Optional[TikTokAccount]:
        db_row = self.session.get(AccountDbTable, account_id)
        if not db_row:
            return None
        return self._to_domain(db_row)

    def get_all(self) -> List[TikTokAccount]:
        statement = select(AccountDbTable)
        results = self.session.exec(statement).all()
        return [self._to_domain(row) for row in results]

    def save(self, account: TikTokAccount) -> TikTokAccount:
        canonical_email = (account.email or account.id or "").strip().lower()
        if not canonical_email:
            raise ValueError("Account email is required and is the primary key.")
        lookup_key = (account.id or canonical_email).strip().lower()
        db_row = self.session.get(AccountDbTable, lookup_key)
        if not db_row:
            db_row = AccountDbTable(
                email=canonical_email,
                username=account.username
            )
        else:
            db_row.username = account.username

        db_row.password = account.password
        db_row.email = canonical_email
        db_row.email_password = account.email_password
        db_row.refresh_token = account.refresh_token       
        db_row.client_id = account.client_id               
        db_row.status = account.status
        db_row.health_status = account.health_status
        db_row.profile_status = account.profile_status
        
        # ĐỒNG BỘ GHI NHẬN CÁC CỘT PHÂN LÔ MỚI XUỐNG SQLITE VẬT LÝ
        db_row.country = account.country
        db_row.batch_tag = account.batch_tag
        db_row.created_at = account.created_at or ""
        db_row.note = account.note or ""
        db_row.upload_success_count = int(account.upload_success_count or 0)
        db_row.upload_failure_count = int(account.upload_failure_count or 0)
        db_row.last_upload_status = account.last_upload_status or "NEVER"
        db_row.last_upload_at = account.last_upload_at or ""
        db_row.last_upload_error = account.last_upload_error or ""
        db_row.video_count = account.video_count
        db_row.follower_count = account.follower_count
        db_row.following_count = account.following_count
        db_row.likes_count = account.likes_count
        db_row.tiktok_user_id = account.tiktok_user_id or ""
        db_row.tiktok_sec_uid = account.tiktok_sec_uid or ""
        db_row.display_name = account.display_name or ""
        db_row.bio = account.bio or ""
        db_row.avatar_url = account.avatar_url or ""
        db_row.verified = bool(account.verified)
        db_row.private_account = bool(account.private_account)
        db_row.website_url = account.website_url or ""
        db_row.total_views = account.total_views
        db_row.total_video_likes = account.total_video_likes
        db_row.total_comments = account.total_comments
        db_row.total_shares = account.total_shares
        db_row.collected_video_count = int(account.collected_video_count or 0)
        db_row.analytics_sync_status = account.analytics_sync_status or "NEVER"
        db_row.analytics_sync_source = account.analytics_sync_source or ""
        db_row.analytics_sync_error = account.analytics_sync_error or ""
        db_row.metrics_updated_at = account.metrics_updated_at or ""
        
        db_row.current_step = account.current_step
        db_row.proxy_id = account.proxy_id
        db_row.cookies_json = json.dumps(account.cookies)

        self.session.add(db_row)
        self.session.commit()
        self.session.refresh(db_row)
        
        account.email = db_row.email
        account.id = db_row.email
        return account

    def save_prioritizing_username(
        self, account: TikTokAccount
    ) -> Tuple[TikTokAccount, Optional[TikTokAccount]]:
        """Give the current account its web-confirmed username.

        If another row owns that username, atomically move that row to the
        current account's previous username. A temporary value avoids violating
        SQLite's immediate UNIQUE constraint during the swap.
        """
        canonical_email = (account.email or account.id or "").strip().lower()
        if not canonical_email:
            raise ValueError("Account email is required and is the primary key.")

        db_row = self.session.get(AccountDbTable, canonical_email)
        desired_username = (account.username or "").strip()
        if not db_row or not desired_username or db_row.username == desired_username:
            return self.save(account), None

        previous_username = db_row.username
        conflict = self.session.exec(
            select(AccountDbTable).where(
                AccountDbTable.username == desired_username,
                AccountDbTable.email != canonical_email,
            )
        ).first()
        if not conflict:
            return self.save(account), None
        if not previous_username:
            raise ValueError(
                f"Cannot displace username owner {conflict.email}: current username is empty."
            )

        try:
            conflict.username = f"__swap__{uuid.uuid4().hex}"
            self.session.add(conflict)
            self.session.flush()

            db_row.username = desired_username
            self.session.add(db_row)
            self.session.flush()

            conflict.username = previous_username
            self.session.add(conflict)
            saved = self.save(account)
            return saved, self._to_domain(conflict)
        except Exception:
            self.session.rollback()
            raise

    def update_status(self, account_id: str, status: str) -> None:
        db_row = self.session.get(AccountDbTable, account_id)
        if db_row:
            db_row.status = status
            self.session.add(db_row)
            self.session.commit()

    def delete(self, account_id: str) -> bool:
        db_row = self.session.get(AccountDbTable, account_id)
        if db_row:
            self.session.delete(db_row)
            self.session.commit()
            return True
        return False

    def _to_domain(self, db_row: AccountDbTable) -> TikTokAccount:
        """Hàm helper chuyển đổi Database Table Model sang Domain Entity thuần túy"""
        return TikTokAccount(
            id=db_row.email,
            username=db_row.username,
            password=db_row.password,
            email=db_row.email,
            email_password=db_row.email_password,
            refresh_token=db_row.refresh_token,             
            client_id=db_row.client_id,                     
            cookies=json.loads(db_row.cookies_json or "[]"),
            status=db_row.status or "IDLE",
            health_status=db_row.health_status or "ALIVE",
            profile_status=db_row.profile_status or "PENDING",
            
            # =================================================================
            # ĐÃ ĐỒNG BỘ SỬA LỖI: Ánh xạ đọc ngược 3 trường phân lô từ SQLite lên thực thể
            # =================================================================
            country=db_row.country or "US",
            batch_tag=db_row.batch_tag or "DEFAULT",
            created_at=db_row.created_at or "",
            note=getattr(db_row, "note", "") or "",
            upload_success_count=getattr(db_row, "upload_success_count", 0) or 0,
            upload_failure_count=getattr(db_row, "upload_failure_count", 0) or 0,
            last_upload_status=getattr(db_row, "last_upload_status", "NEVER") or "NEVER",
            last_upload_at=getattr(db_row, "last_upload_at", "") or "",
            last_upload_error=getattr(db_row, "last_upload_error", "") or "",
            video_count=getattr(db_row, "video_count", None),
            follower_count=getattr(db_row, "follower_count", None),
            following_count=getattr(db_row, "following_count", None),
            likes_count=getattr(db_row, "likes_count", None),
            tiktok_user_id=getattr(db_row, "tiktok_user_id", "") or "",
            tiktok_sec_uid=getattr(db_row, "tiktok_sec_uid", "") or "",
            display_name=getattr(db_row, "display_name", "") or "",
            bio=getattr(db_row, "bio", "") or "",
            avatar_url=getattr(db_row, "avatar_url", "") or "",
            verified=bool(getattr(db_row, "verified", False)),
            private_account=bool(getattr(db_row, "private_account", False)),
            website_url=getattr(db_row, "website_url", "") or "",
            total_views=getattr(db_row, "total_views", None),
            total_video_likes=getattr(db_row, "total_video_likes", None),
            total_comments=getattr(db_row, "total_comments", None),
            total_shares=getattr(db_row, "total_shares", None),
            collected_video_count=getattr(db_row, "collected_video_count", 0) or 0,
            analytics_sync_status=getattr(db_row, "analytics_sync_status", "NEVER") or "NEVER",
            analytics_sync_source=getattr(db_row, "analytics_sync_source", "") or "",
            analytics_sync_error=getattr(db_row, "analytics_sync_error", "") or "",
            metrics_updated_at=getattr(db_row, "metrics_updated_at", "") or "",

            current_step=db_row.current_step,
            proxy_id=db_row.proxy_id
        )
