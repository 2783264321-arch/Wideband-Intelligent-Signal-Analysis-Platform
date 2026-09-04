from pathlib import Path

from app.core.errors import PlatformError


class StorageService:
    def __init__(self, data_root: Path):
        self.data_root = data_root.resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)

    def _safe_child(self, *parts: str) -> Path:
        for part in parts:
            if not part or part in {".", ".."} or "/" in part or "\\" in part:
                raise PlatformError("INVALID_PATH", "Storage path component is invalid.")
        path = self.data_root.joinpath(*parts).resolve()
        if self.data_root not in path.parents and path != self.data_root:
            raise PlatformError("INVALID_PATH", "Storage path escapes the data root.")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def recording_dir(self, recording_id: str) -> Path:
        return self._safe_child("recordings", recording_id)

    def artifact_dir(self, run_id: str) -> Path:
        return self._safe_child("artifacts", run_id)

    def spectrogram_cache_dir(self) -> Path:
        return self._safe_child("cache", "spectrograms")

    def import_temp_dir(self, token: str) -> Path:
        return self._safe_child("imports", token)
