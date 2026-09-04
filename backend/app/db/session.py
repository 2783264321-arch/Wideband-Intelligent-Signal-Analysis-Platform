from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


class Database:
    def __init__(self, database_url: str):
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)

    def get_session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            yield session


def get_session() -> Iterator[Session]:
    settings = Settings()
    database = Database(settings.database_url)
    yield from database.get_session()
