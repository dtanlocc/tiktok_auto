from sqlmodel import Session, SQLModel, create_engine

from app.domain.entities.account import TikTokAccount
from app.infrastructure.database.sqlite_repository import SQLiteAccountRepository


def test_web_confirmed_username_atomically_swaps_conflicting_owner():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        repository = SQLiteAccountRepository(session)
        current = repository.save(TikTokAccount(
            id="current@example.com",
            email="current@example.com",
            username="old_username",
        ))
        repository.save(TikTokAccount(
            id="other@example.com",
            email="other@example.com",
            username="web_username",
        ))

        current.username = "web_username"
        saved, displaced = repository.save_prioritizing_username(current)

        assert saved.username == "web_username"
        assert displaced is not None
        assert displaced.email == "other@example.com"
        assert displaced.username == "old_username"
        assert repository.get_by_id("current@example.com").username == "web_username"
        assert repository.get_by_id("other@example.com").username == "old_username"
