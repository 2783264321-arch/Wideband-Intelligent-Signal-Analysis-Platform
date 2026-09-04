from app.core.errors import PlatformError
from app.labels.service import LabelSpaceService
from app.recordings.model import RecordingModel


def validate_physical_box(
    recording: RecordingModel,
    *,
    t_start_s: float,
    t_end_s: float,
    f_low_hz: float,
    f_high_hz: float,
    error_code: str,
) -> None:
    if not (0.0 <= t_start_s < t_end_s <= recording.duration_s):
        raise PlatformError(error_code, "Time bounds must lie within the recording and have positive duration.")
    if not (recording.frequency_low_hz <= f_low_hz < f_high_hz <= recording.frequency_high_hz):
        raise PlatformError(error_code, "Frequency bounds must lie within the recording and have positive bandwidth.")


def validate_label(
    label_service: LabelSpaceService,
    *,
    label_space_id: str,
    class_id: int,
    class_name: str,
    error_code: str,
) -> None:
    label_space = label_service.get(label_space_id)
    match = next((item for item in label_space.classes if item.id == class_id), None)
    if match is None or match.name != class_name:
        raise PlatformError(error_code, "Class id/name does not match the selected label space.")
