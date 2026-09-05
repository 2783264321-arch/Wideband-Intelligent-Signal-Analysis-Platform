from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_root = tmp_path / "data"
    label_root = Path(__file__).resolve().parents[2] / "label_spaces"
    return Settings(
        project_root=tmp_path,
        data_root=data_root,
        label_space_root=label_root,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session(settings: Settings):
    from app.db.base import Base, load_domain_models
    from app.db.migrations import run_additive_migrations
    from app.db.session import Database

    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)
    with database.session_factory() as db_session:
        yield db_session
