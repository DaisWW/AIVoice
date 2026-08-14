from typing import Any, Literal

from pydantic import BaseModel, Field


class JobRename(BaseModel):
    name: str


class VoiceUpdate(BaseModel):
    name: str
    notes: str = ""


class VoiceFileUpdate(BaseModel):
    enabled: bool | None = None
    emotion_tag: str | None = None


class CandidateRegenerate(BaseModel):
    name: str = ""
    text: str
    pronunciation: str
    direction: Literal["auto", "flat", "rise", "fall"] = "auto"
    source_candidate_id: str | None = None
    seed: int | None = None
    generation_settings: dict[str, Any] = Field(default_factory=dict)


class CandidateAccept(BaseModel):
    candidate_id: str
