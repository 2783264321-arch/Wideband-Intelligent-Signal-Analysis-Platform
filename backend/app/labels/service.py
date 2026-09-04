from dataclasses import dataclass
from json import loads
from pathlib import Path

from app.core.errors import PlatformError


@dataclass(frozen=True)
class LabelClass:
    id: int
    name: str


@dataclass(frozen=True)
class LabelSpace:
    id: str
    version: int
    classes: tuple[LabelClass, ...]


class LabelSpaceService:
    def __init__(self, root: Path):
        self.root = Path(root)

    def get(self, label_space_id: str) -> LabelSpace:
        path = self.root / f"{label_space_id}.json"
        if not path.is_file():
            raise PlatformError("LABEL_SPACE_NOT_FOUND", f"Label space '{label_space_id}' was not found.", 404)
        raw = loads(path.read_text(encoding="utf-8"))
        classes = tuple(LabelClass(id=int(item["id"]), name=str(item["name"])) for item in raw["classes"])
        return LabelSpace(id=str(raw["id"]), version=int(raw["version"]), classes=classes)
