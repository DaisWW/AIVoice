from __future__ import annotations

from pathlib import Path
import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.engines import cosyvoice, qwen_tts
from app.engines.contracts import ModelStatus
from app.engines.cosyvoice import CosyVoice3Adapter
from app.engines.gpt_sovits import GptSovitsAdapter
from app.engines.qwen_tts import Qwen3TtsAdapter
from app.engines.registry import VoiceEngine
from app.api.routes.system import readiness
from app.profiles import Profiles


class _AvailableAdapter:
    engine_id = "optional"
    is_loaded = False

    def __init__(self) -> None:
        self.status_calls = 0

    def status(self, profile):
        del profile
        self.status_calls += 1
        return ModelStatus(True)


class _UnavailableAdapter(_AvailableAdapter):
    def status(self, profile):
        del profile
        self.status_calls += 1
        raise RuntimeError("model bootstrap failed")


def test_registry_respects_explicit_model_disable() -> None:
    adapter = _AvailableAdapter()
    engine = VoiceEngine.__new__(VoiceEngine)
    engine._adapters = {adapter.engine_id: adapter}

    status = engine._status(
        {
            "engine": adapter.engine_id,
            "available": False,
            "availability_reason": "暂未开放",
        }
    )

    assert status == ModelStatus(False, False, "暂未开放")
    assert adapter.status_calls == 0


def test_registry_model_status_includes_model_identity() -> None:
    adapter = _AvailableAdapter()
    engine = VoiceEngine.__new__(VoiceEngine)
    engine._profiles = Profiles(
        {
            "models": [
                {
                    "id": "optional-model",
                    "label": "Optional Model",
                    "engine": adapter.engine_id,
                }
            ]
        }
    )
    engine._adapters = {adapter.engine_id: adapter}

    model = engine.model_status()["models"]["optional-model"]

    assert model["id"] == "optional-model"
    assert model["label"] == "Optional Model"
    assert model["available"] is True


def test_registry_marks_required_initialization_failure_unavailable() -> None:
    adapter = _UnavailableAdapter()
    engine = VoiceEngine.__new__(VoiceEngine)
    engine._profiles = Profiles(
        {
            "models": [
                {
                    "id": "required-model",
                    "label": "Required Model",
                    "engine": adapter.engine_id,
                    "required": True,
                }
            ]
        }
    )
    engine._adapters = {adapter.engine_id: adapter}

    status = engine.model_status()

    assert status["unavailable_required_models"] == ["required-model"]
    assert status["models"]["required-model"]["available"] is False
    assert status["models"]["required-model"]["availability_reason"] == "模型状态检查失败"


def test_registry_status_logs_are_sanitized(caplog) -> None:
    adapter = _UnavailableAdapter()
    adapter.status = lambda profile: (_ for _ in ()).throw(
        RuntimeError(r"C:\private\api_key=secret")
    )
    engine = VoiceEngine.__new__(VoiceEngine)
    engine._profiles = Profiles(
        {
            "models": [
                {
                    "id": "required-model",
                    "label": "Required Model",
                    "engine": adapter.engine_id,
                    "required": True,
                }
            ]
        }
    )
    engine._adapters = {adapter.engine_id: adapter}

    with caplog.at_level(logging.ERROR, logger="app.engines.registry"):
        engine.model_status()

    assert "secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_registry_marks_required_profile_disable_unavailable() -> None:
    adapter = _AvailableAdapter()
    engine = VoiceEngine.__new__(VoiceEngine)
    engine._profiles = Profiles(
        {
            "models": [
                {
                    "id": "required-model",
                    "label": "Required Model",
                    "engine": adapter.engine_id,
                    "required": True,
                    "available": False,
                    "availability_reason": "disabled",
                }
            ]
        }
    )
    engine._adapters = {adapter.engine_id: adapter}

    status = engine.model_status()

    assert status["unavailable_required_models"] == ["required-model"]
    assert status["models"]["required-model"]["available"] is False
    assert adapter.status_calls == 0


@pytest.mark.parametrize(
    "engine_status",
    (
        {"missing_models": [], "unavailable_required_models": ["required-model"]},
        RuntimeError("model bootstrap failed"),
    ),
)
def test_readiness_rejects_required_model_unavailable(engine_status) -> None:
    def model_status():
        if isinstance(engine_status, Exception):
            raise engine_status
        return engine_status

    services = SimpleNamespace(
        job_queue=SimpleNamespace(is_running=True),
        engine=SimpleNamespace(model_status=model_status),
    )

    with pytest.raises(HTTPException) as error:
        readiness(services)

    assert error.value.status_code == 503


def test_gpt_status_tracks_the_loaded_model_version(tmp_path: Path) -> None:
    clone = SimpleNamespace(
        model_paths=lambda root, version: {},
        missing_models=lambda paths: [],
    )
    adapter = GptSovitsAdapter(
        SimpleNamespace(root=tmp_path),
        {"voice_clone": {}},
    )
    adapter._clone = clone
    adapter._tts = object()
    adapter._loaded_version = "v2"

    assert adapter.status({"model_version": "v2"}).loaded is True
    assert adapter.status({"model_version": "v2ProPlus"}).loaded is False


def test_qwen_status_requires_tokenizer_files(tmp_path: Path, monkeypatch) -> None:
    adapter = Qwen3TtsAdapter(SimpleNamespace(root=tmp_path))
    model_path = tmp_path / "qwen-model"
    for name in set(adapter._REQUIRED_FILES) - {"tokenizer_config.json"}:
        path = model_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(qwen_tts, "missing_modules", lambda names: ())
    monkeypatch.setattr(adapter, "_transformers_supported", lambda: True)

    status = adapter.status({"model_path": str(model_path)})

    assert status.missing_files == (str(model_path / "tokenizer_config.json"),)


def test_cosy_status_allows_missing_optional_speaker_catalog(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = CosyVoice3Adapter(SimpleNamespace(root=tmp_path))
    source = tmp_path / "tools" / "CosyVoice" / "cosyvoice" / "cli" / "cosyvoice.py"
    source.parent.mkdir(parents=True)
    source.touch()
    model_path = tmp_path / "cosy-model"
    for name in set(adapter._REQUIRED_FILES) - {"spk2info.pt"}:
        path = model_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(cosyvoice, "missing_modules", lambda names: ())

    status = adapter.status({"model_path": str(model_path)})

    assert status.available is True


@pytest.mark.parametrize(
    ("adapter_type", "adapter_module"),
    (
        (Qwen3TtsAdapter, qwen_tts),
        (CosyVoice3Adapter, cosyvoice),
    ),
)
def test_adapter_releases_previous_model_before_switching_path(
    tmp_path, monkeypatch, adapter_type, adapter_module
) -> None:
    adapter = adapter_type(SimpleNamespace(root=tmp_path))
    adapter._model = object()
    adapter._loaded_path = tmp_path / "old-model"
    if isinstance(adapter, Qwen3TtsAdapter):
        adapter._prompt = object()
        adapter._prompt_key = ("reference.wav", 1)
    monkeypatch.setattr(adapter_module, "release_cuda_memory", lambda: None)
    monkeypatch.setattr(
        adapter,
        "status",
        lambda profile: ModelStatus(False, reason="expected stop"),
    )

    with pytest.raises(RuntimeError, match="expected stop"):
        adapter._load({"model_path": "new-model"})

    assert adapter._model is None
    assert adapter._loaded_path is None
    if isinstance(adapter, Qwen3TtsAdapter):
        assert adapter._prompt is None
        assert adapter._prompt_key is None


@pytest.mark.parametrize(
    ("installed", "supported"),
    (
        ("4.57.3", True),
        ("4.57.3.dev0", False),
        ("4.57.3+cuda", True),
        ("not-a-version", False),
    ),
)
def test_qwen_transformers_version_is_parsed_safely(
    monkeypatch, installed: str, supported: bool
) -> None:
    monkeypatch.setattr(qwen_tts, "version", lambda package: installed)

    assert Qwen3TtsAdapter._transformers_supported() is supported


def test_qwen_voice_prompt_uses_reference_text_when_available(tmp_path: Path) -> None:
    class Model:
        def create_voice_clone_prompt(self, **options):
            return options

    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"wav")
    adapter = Qwen3TtsAdapter(SimpleNamespace(root=tmp_path))

    prompt = adapter._voice_prompt(Model(), reference, " 参考台词。 ")

    assert prompt["ref_text"] == "参考台词。"
    assert prompt["x_vector_only_mode"] is False


def test_qwen_voice_prompt_falls_back_without_reference_text(tmp_path: Path) -> None:
    class Model:
        def create_voice_clone_prompt(self, **options):
            return options

    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"wav")
    adapter = Qwen3TtsAdapter(SimpleNamespace(root=tmp_path))

    prompt = adapter._voice_prompt(Model(), reference, "")

    assert "ref_text" not in prompt
    assert prompt["x_vector_only_mode"] is True
