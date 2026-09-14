from pathlib import Path
import re

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_RUNTIME_FAMILY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WSP_", extra="ignore", populate_by_name=True)

    project_root: Path = Path(__file__).resolve().parents[3]
    data_root: Path | None = None
    label_space_root: Path | None = None
    database_url: str | None = None

    # Explicit local inference runtime config (D2). Plugin inference must run in a
    # separate ML interpreter, never the control-plane interpreter.
    local_cpu_python_path: Path | None = None  # WSP_LOCAL_CPU_PYTHON_PATH
    local_gpu_python_path: Path | None = None  # WSP_LOCAL_GPU_PYTHON_PATH
    local_cpu_runtime_ref: str | None = None  # WSP_LOCAL_CPU_RUNTIME_REF (immutable generation)
    local_gpu_runtime_ref: str | None = None  # WSP_LOCAL_GPU_RUNTIME_REF
    local_inference_work_root: Path | None = None  # WSP_LOCAL_INFERENCE_WORK_ROOT
    # Operator-owned logical installation/runtime family (never a hostname/serial).
    runtime_family: str | None = None  # WSP_RUNTIME_FAMILY
    # Namespaced trusted local asset mapping (release-bound plugins only):
    # {"<plugin_id>/<plugin_version>/<asset_manifest_sha256>": {logical: /abs/path}}
    local_asset_paths: dict | None = Field(
        default=None, validation_alias="WSP_LOCAL_ASSET_PATHS_JSON"
    )

    def model_post_init(self, __context) -> None:
        project_root = self.project_root.resolve()
        if self.data_root is None:
            self.data_root = project_root / "data"
        if self.label_space_root is None:
            self.label_space_root = project_root / "label_spaces"
        if self.database_url is None:
            self.database_url = f"sqlite:///{project_root / 'platform.db'}"

    @field_validator("runtime_family")
    @classmethod
    def _validate_runtime_family(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _RUNTIME_FAMILY_RE.fullmatch(value) is None:
            raise ValueError(
                "runtime_family must be a safe logical identifier "
                "(^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$)."
            )
        return value
