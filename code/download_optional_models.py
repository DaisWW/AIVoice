from __future__ import annotations

import argparse
from pathlib import Path

from modelscope import snapshot_download


MODELS = {
    "qwen": {
        "repo_id": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "directory": "qwen3_tts_0_6b_base",
        "ignore_patterns": (),
    },
    "qwen17": {
        "repo_id": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "directory": "qwen3_tts_1_7b_base",
        "ignore_patterns": (),
    },
    "cosy": {
        "repo_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        "directory": "cosyvoice3_0_5b",
        "ignore_patterns": (
            "asset/*",
            "flow.decoder.estimator.fp32.onnx",
            "llm.rl.pt",
            "speech_tokenizer_v3.batch.onnx",
            "vllm/*",
        ),
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 Voice Lab 可选声音模型")
    parser.add_argument("models", nargs="*", choices=sorted(MODELS), default=[])
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    selected = args.models or list(MODELS)
    destination = args.root.resolve() / "tools" / "models"
    destination.mkdir(parents=True, exist_ok=True)

    for name in selected:
        config = MODELS[name]
        target = destination / str(config["directory"])
        print(f"[{name}] {config['repo_id']} -> {target}", flush=True)
        snapshot_download(
            model_id=str(config["repo_id"]),
            local_dir=str(target),
            ignore_patterns=list(config["ignore_patterns"]),
        )
        print(f"[{name}] 完成", flush=True)


if __name__ == "__main__":
    main()
