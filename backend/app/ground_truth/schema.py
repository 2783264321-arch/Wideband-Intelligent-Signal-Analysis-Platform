from pydantic import BaseModel, ConfigDict


class GroundTruthObjectIn(BaseModel):
    id: str
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str


class GroundTruthImport(BaseModel):
    label_space: str
    objects: list[GroundTruthObjectIn]


class GroundTruthRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    recording_id: str
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str
