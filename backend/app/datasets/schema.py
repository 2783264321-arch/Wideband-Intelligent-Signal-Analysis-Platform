from pydantic import BaseModel, Field


class RegisterSpaceNetRequest(BaseModel):
    dataset_path: str = Field(min_length=1)
    split: str = Field(default="test", pattern=r"^(train|test)$")


class RegistrationSummaryRead(BaseModel):
    created: int
    skipped: int
    invalid: int
    total: int