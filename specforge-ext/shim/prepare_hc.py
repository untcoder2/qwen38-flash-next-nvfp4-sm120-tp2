# coding=utf-8
"""Offline HC-stream feature preparation for the Qwen4Exp MTP head.

Wraps ``scripts/prepare_hidden_states.py`` instead of forking it: the data
pipeline (dataset build, DP sharding, vocab mapping, async .ckpt writer) is
SpecForge's own; only the capture target is replaced with
``OfflineHCCaptureBackend`` so the writer persists
``target_hc_hidden_states`` (the raw ``hc_count*hidden_size`` stream) under the
``mtp_hc`` capture layout registered by ``specforge_ext.shim``.

Usage (from ~/specforge, torchrun-style env as in the SpecForge docs):

    python -m specforge_ext.shim.prepare_hc \
        --target-model-path /home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp \
        --data-path  <sharegpt-style jsonl per scripts/prepare_data.py> \
        --output-path ./cache/hidden_states/qwen38-flash-next-mtp \
        --draft-model-config ~/specforge/specforge_ext/configs/qwen4-exp-flash-next-mtp.json \
        --strategy mtp_hc --chat-template qwen --max-length 2048 \
        --tp-size 1 --batch-size 16

(--strategy defaults to mtp_hc when this launcher injects argv.)
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from specforge_ext.shim import install


def _prepare_script_path() -> Path:
    import specforge  # package dir: ~/specforge/specforge

    root = Path(specforge.__file__).resolve().parent.parent
    script = root / "scripts" / "prepare_hidden_states.py"
    if not script.is_file():
        raise FileNotFoundError(f"SpecForge preparation script missing: {script}")
    return script


def _load_prepare_module():
    path = _prepare_script_path()
    spec = importlib.util.spec_from_file_location("sf_prepare_hidden_states", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _hc_loader(pretrained_model_name_or_path, **kwargs):
    """Replacement for ``load_offline_capture`` building the HC capture backend."""

    from specforge.offline_capture import OfflineSGLangCapture

    from specforge_ext.shim.capture_hc import OfflineHCCaptureBackend

    backend = OfflineHCCaptureBackend.build(
        pretrained_model_name_or_path, **kwargs
    )
    return OfflineSGLangCapture(backend)


def main(argv: list[str] | None = None) -> int:
    install()
    module = _load_prepare_module()
    module.load_offline_capture = _hc_loader

    args = list(sys.argv[1:] if argv is None else argv)
    if "--strategy" not in args:
        args = ["--strategy", "mtp_hc", *args]
    sys.argv = [sys.argv[0], *args]
    module.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
