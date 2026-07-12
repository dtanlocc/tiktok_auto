from fastapi import Depends, Request
from sqlmodel import Session
from app.infrastructure.database.connection import get_db_session
from app.infrastructure.database.sqlite_repository import SQLiteAccountRepository, SQLiteProxyRepository
from app.domain.ports.repository import IAccountRepository, IProxyRepository
from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher
from app.infrastructure.scheduler.interaction_scheduler import InteractionScheduler

def get_account_repository(
    session: Session = Depends(get_db_session)
) -> IAccountRepository:
    return SQLiteAccountRepository(session)

def get_proxy_repository(
    session: Session = Depends(get_db_session)
) -> IProxyRepository:
    return SQLiteProxyRepository(session)

def get_task_dispatcher(request: Request) -> ConcurrentTaskDispatcher:
    """Lấy Singleton Instance của Task Dispatcher từ App State"""
    return request.app.state.dispatcher

def get_interaction_scheduler(request: Request) -> InteractionScheduler:
    """Lấy Singleton Instance của Interaction Scheduler từ App State"""
    return request.app.state.interaction_scheduler