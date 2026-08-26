from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.@-]+$")
    display_name: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=128)


class UserStatusUpdate(BaseModel):
    status: Literal["active", "disabled"]


class PasswordReset(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    prompt: str = Field(default="", max_length=12000)


class ProjectUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    prompt: str | None = Field(default=None, max_length=12000)


class MemberCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class MemberRoleUpdate(BaseModel):
    role: Literal["admin", "member"]


class JobRename(BaseModel):
    name: str


class VoiceUpdate(BaseModel):
    name: str
    notes: str = ""


class VoiceFileUpdate(BaseModel):
    enabled: bool | None = None
    emotion_tag: str | None = None
    reference_text: str | None = Field(default=None, max_length=2000)


class ScriptUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    prompt: str | None = Field(default=None, max_length=12000)


class PromptSuggestionRequest(BaseModel):
    goal: str = Field(default="", max_length=2000)


class TextGenerationRequest(BaseModel):
    instruction: str = Field(default="", max_length=4000)
    line_count: int = Field(default=5, ge=1, le=100)
    model_id: str = Field(default="", max_length=64)


class TextModelUpdate(BaseModel):
    enabled: bool = False
    label: str = Field(default="文本台词模型", min_length=1, max_length=100)
    base_url: str = Field(default="", max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    clear_api_key: bool = False
    model: str = Field(default="", max_length=150)
    protocol: Literal["responses", "chat_completions"] = "responses"
    reasoning_effort: Literal[
        "", "none", "minimal", "low", "medium", "high", "max"
    ] = ""
    timeout_seconds: int = Field(default=180, ge=10, le=600)
    max_output_tokens: int = Field(default=4000, ge=128, le=32000)
    temperature: float = Field(default=0.7, ge=0, le=2)


class ScriptItemUpdate(BaseModel):
    text: str = Field(default="", max_length=2000)
    pronunciation: str = Field(default="", max_length=2000)


class ScriptItemsUpdate(BaseModel):
    items: list[ScriptItemUpdate] = Field(min_length=1, max_length=3000)


class ElevenLabsProviderUpdate(BaseModel):
    enabled: bool
    api_key: str | None = Field(default=None, max_length=500)
    clear_api_key: bool = False
    base_url: str = Field(min_length=1, max_length=500)
    tts_model_id: str = Field(min_length=1, max_length=100)
    output_format: Literal["wav_44100", "wav_48000"] = "wav_48000"
    request_timeout_seconds: int = Field(default=180, ge=10, le=600)
    remove_background_noise: bool = False
    stability: float = Field(default=0.5, ge=0, le=1)
    similarity_boost: float = Field(default=0.85, ge=0, le=1)
    style: float = Field(default=0, ge=0, le=1)
    use_speaker_boost: bool = True


class MiniMaxProviderUpdate(BaseModel):
    enabled: bool
    api_key: str | None = Field(default=None, max_length=500)
    clear_api_key: bool = False
    base_url: str = Field(min_length=1, max_length=500)
    tts_model_id: str = Field(min_length=1, max_length=100)
    output_format: Literal["wav"] = "wav"
    sample_rate: Literal[8000, 16000, 22050, 24000, 32000, 44100] = 32000
    request_timeout_seconds: int = Field(default=180, ge=10, le=600)
    language_boost: str = Field(default="auto", min_length=1, max_length=32)
    speed: float = Field(default=1, ge=0.5, le=2)
    volume: float = Field(default=1, gt=0, le=10)
    pitch: int = Field(default=0, ge=-12, le=12)
    emotion: str = Field(default="", max_length=32)
    need_noise_reduction: bool = False
    need_volume_normalization: bool = False


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
