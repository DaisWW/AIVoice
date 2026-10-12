from __future__ import annotations

import numpy as np
import pytest
from types import SimpleNamespace
from pathlib import Path

from app.engines import cosyvoice
from app.engines.contracts import ReferenceAudio
from app.engines.cosyvoice import CosyVoice3Adapter
from voice_core.cosyvoice_pronunciation import cosyvoice_text
from voice_core.pronunciation import analyze_pronunciation


def test_original_syllables_do_not_merge_ra_and_la() -> None:
    pronunciation = "ra-la，miu-na↗"
    analysis = analyze_pronunciation(pronunciation)

    assert cosyvoice_text(pronunciation, analysis.generated_text) == (
        "[r][a] [l][a]，[m][iu] [n][a]？"
    )


def test_emphasis_uses_native_markup_and_holds_do_not_repeat_units() -> None:
    pronunciation = "GU——la……gu-la-da。↘"
    analysis = analyze_pronunciation(pronunciation)

    assert cosyvoice_text(pronunciation, analysis.generated_text) == (
        "<strong>[g][u]</strong> [l][a]……[g][u] [l][a] [d][a]。"
    )


def test_explicit_native_phonemes_are_not_reinterpreted() -> None:
    pronunciation = "raw: [M][OW1] [L][AA1] [N][AA1]"
    analysis = analyze_pronunciation(pronunciation)

    assert cosyvoice_text(pronunciation, analysis.generated_text) == (
        "[M][OW1] [L][AA1] [N][AA1]."
    )


def test_candidate_tail_direction_overrides_original_punctuation() -> None:
    assert cosyvoice_text("ra-la？↗", "啦啦。") == "[r][a] [l][a]。"


@pytest.mark.parametrize("emitted", (39, 40))
def test_cosy_budget_detection_restores_decoder_and_rejects_partial_output(
    tmp_path: Path, monkeypatch, emitted: int
) -> None:
    class Speech:
        def detach(self):
            return self

        def float(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.ones(100, dtype=np.float32) * 0.1

    class Llm:
        def inference_wrapper(self, lm_input, sampling, min_len, max_len, uuid):
            yield from range(emitted)

    llm = Llm()
    original = llm.inference_wrapper
    inputs = []

    def frontend(text, *args):
        inputs.append(text)
        return {}

    def tts(**options):
        list(llm.inference_wrapper(None, 25, 2, 40, "test"))
        yield {"tts_speech": Speech()}

    model = SimpleNamespace(
        frontend=SimpleNamespace(frontend_cross_lingual=frontend),
        model=SimpleNamespace(llm=llm, tts=tts),
        sample_rate=24_000,
    )
    adapter = CosyVoice3Adapter(SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(adapter, "_load", lambda profile: model)
    monkeypatch.setattr(cosyvoice, "set_generation_seed", lambda seed: None)
    output = tmp_path / "output.wav"
    args = (
        {"pronunciation": "ra-la", "generated_text": "啦啦。"},
        ReferenceAudio(tmp_path / "reference.wav", "", "", 4.0),
        {},
        123,
        output,
        {},
    )

    if emitted == 40:
        with pytest.raises(RuntimeError, match="仍未自然结束"):
            adapter.generate(*args)
        assert not output.exists()
    else:
        result = adapter.generate(*args)
        assert result.audio_path == output
        assert output.is_file()
    assert llm.inference_wrapper == original
    assert inputs == [adapter._INSTRUCT_PREFIX + "[r][a] [l][a]。"]
