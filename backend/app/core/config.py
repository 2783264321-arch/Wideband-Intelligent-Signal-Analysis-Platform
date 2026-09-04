from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WSP_", extra="ignore")

    project_root: Path = Path(__file__).resolve().parents[3]
    data_root: Path | None = None
    label_space_root: Path | None = None
    database_url: str | None = None

    def model_post_init(self, __context) -> None:
        project_root = self.project_root.resolve()
        if self.data_root is None:
            self.data_root = project_root / "data"
        if self.label_space_root is None:
            self.label_space_root = project_root / "label_spaces"
        if self.database_url is None:
            self.database_url = f"sqlite:///{project_root / 'platform.db'}"
