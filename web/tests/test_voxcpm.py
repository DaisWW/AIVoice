from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.engines import voxcpm
from app.engines.contracts import ReferenceAudio
from app.engines.voxcpm import VoxCpmAdapter


def _model_files(adapter: VoxCpmAdapter, model_path: Path) -> None:
    for name in adapter._REQUIRED_FILES:
        path = model_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def test_voxcpm_status_checks_runtime_and_weights(tmp_path: Path, monkeypatch) -> None:
    adapter = VoxCpmAdapter(SimpleNamespace(root=tmp_path))
    model_path = tmp_path / "voxcpm"
    _model_files(adapter, model_path)
    (model_path / "config.json").write_text(
        json.dumps({"architecture": "voxcpm2"}), encoding="utf-8"
    )
    monkeypatch.setattr(voxcpm, "missing_modules", lambda names: ())

    status = adapter.status({"model_path": str(model_path)})

    assert status.available is True
    assert status.missing_files == ()


def test_voxcpm2_generate_uses_reference_audio(tmp_path: Path, monkeypatch) -> None:
    class Model:
        class Tts:
            sample_rate = 48_000

        tts_model = Tts()

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return np.zeros(480, dtype=np.float32)

    reference_path = tmp_path / "reference.wav"
    reference_path.write_bytes(b"wav")
    output_path = tmp_path / "out.wav"
    model = Model()
    adapter = VoxCpmAdapter(SimpleNamespace(root=tmp_path))
    adapter._model = model
    adapter._loaded_path = tmp_path / "tools" / "models" / "voxcpm2"
    adapter._architecture = "voxcpm2"
    monkeypatch.setattr(voxcpm, "set_generation_seed", lambda seed: None)

    result = adapter.generate(
        {"generated_text": "测试——台词"},
        ReferenceAudio(reference_path, "", "all_zh", 3.0),
        {},
        42,
        output_path,
        {},
    )

    assert output_path.is_file()
    assert result.processing_backend == "voxcpm-voxcpm2"
    assert model.calls[0]["text"] == "测试台词"
    assert model.calls[0]["reference_wav_path"] == str(reference_path)
    assert "prompt_text" not in model.calls[0]


def test_voxcpm15_requires_reference_transcript(tmp_path: Path, monkeypatch) -> None:
    class Model:
        class Tts:
            sample_rate = 44_100

        tts_model = Tts()

        def generate(self, **kwargs):
            raise AssertionError("should not generate without a transcript")

    reference_path = tmp_path / "reference.wav"
    reference_path.write_bytes(b"wav")
    adapter = VoxCpmAdapter(SimpleNamespace(root=tmp_path))
    adapter._model = Model()
    adapter._loaded_path = tmp_path / "tools" / "models" / "voxcpm2"
    adapter._architecture = "voxcpm"
    monkeypatch.setattr(voxcpm, "set_generation_seed", lambda seed: None)

    with pytest.raises(RuntimeError, match="准确逐字稿"):
        adapter.generate(
            {"generated_text": "测试"},
            ReferenceAudio(reference_path, "", "all_zh", 3.0),
            {},
            42,
            tmp_path / "out.wav",
            {},
        )
