from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def load_domain_models() -> None:
    # Import side effects register model metadata with Base.
    from app.analysis import model as _analysis  # noqa: F401
    from app.detections import model as _detections  # noqa: F401
    from app.ground_truth import model as _ground_truth  # noqa: F401
    from app.recordings import model as _recordings  # noqa: F401
