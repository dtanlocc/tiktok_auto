import json
from typing import List, Optional
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
        db_row = self.session.get(AccountDbTable, account.id) if account.id else None
        if not db_row:
            db_row = AccountDbTable(id=account.id, username=account.username)

        db_row.password = account.password
        db_row.status = account.status
        db_row.proxy_id = account.proxy_id
        db_row.cookies_json = json.dumps(account.cookies)

        self.session.add(db_row)
        self.session.commit()
        self.session.refresh(db_row)
        
        account.id = db_row.id
        return account

    def update_status(self, account_id: str, status: str) -> None:
        db_row = self.session.get(AccountDbTable, account_id)
        if db_row:
            db_row.status = status
            self.session.add(db_row)
            self.session.commit()

    def _to_domain(self, db_row: AccountDbTable) -> TikTokAccount:
        """Hàm helper chuyển đổi Database Table Model sang Domain Entity thuần túy"""
        return TikTokAccount(
            id=db_row.id,
            username=db_row.username,
            password=db_row.password,
            cookies=json.loads(db_row.cookies_json or "[]"),
            status=db_row.status,
            proxy_id=db_row.proxy_id
        )