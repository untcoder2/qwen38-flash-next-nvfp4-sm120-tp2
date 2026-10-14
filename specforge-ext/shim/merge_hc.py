# coding=utf-8
"""Merge a trained mtp_hc draft checkpoint back into the Qwen4Exp base model.

Thin variant of ``scripts/merge_mtp_to_base.py``: same machinery from
``specforge.export.mtp`` but the runtime-state loader also accepts the
``mtp_hc`` strategy tag. Key layout stays the native flat ``mtp.*`` form that
the fork's ``Qwen4ExpForCausalLMMTP.load_weights`` consumes (``mtp.`` ->
``model.``, fused ``experts.gate_up_proj``/``experts.down_proj`` stacked
tensors, per-head q|gate packing).

Usage (from ~/specforge):

    python -m specforge_ext.shim.merge_hc \
        --base-model-path /home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp \
        --mtp-checkpoint-path ./outputs/qwen38-flash-next-mtp/RUN-latest \
        --draft-config ~/specforge/specforge_ext/configs/qwen4-exp-flash-next-mtp.json \
        --output-path ~/models/Qwen3.8-Flash-Next-NVFP4-mtp-ft
"""

from __future__ import annotations

import argparse
import os

from specforge_ext.shim import install


def merge_mtp_hc_into_base(
    base_model_path: str,
    mtp_checkpoint_path: str,
    output_path: str,
    key_format: str = "sglang",
    draft_config_path: str | None = None,
) -> None:
    install()

    from specforge.export import mtp as export_mtp

    accepted = ("mtp", "mtp_hc")

    def _load_mtp_source(checkpoint_path, cfg_path):
        # Same as specforge.export.mtp._load_mtp_source, but a runtime
        # training checkpoint may carry strategy="mtp_hc".
        path = checkpoint_path
        if path.startswith("file://"):
            path = path[len("file://") :]
        if export_mtp._has_model_weights(path):
            return export_mtp._load_first_checkpoint(path), path, path
        from specforge.export.checkpoint_io import resolve_training_state

        state = resolve_training_state(checkpoint_path)
        if state.get("strategy") not in accepted:
            raise ValueError(
                "MTP-HC merge requires a training checkpoint written by "
                f"strategy='mtp_hc' (or 'mtp'); got {state.get('strategy')!r}"
            )
        draft_state = state.get("draft_state_dict")
        if not isinstance(draft_state, dict):
            raise ValueError("MTP-HC training checkpoint has no draft_state_dict")
        if not cfg_path or not os.path.isfile(cfg_path):
            raise FileNotFoundError(f"draft config not found: {cfg_path}")
        return dict(draft_state), cfg_path, None

    original = export_mtp._load_mtp_source
    export_mtp._load_mtp_source = _load_mtp_source
    try:
        export_mtp.merge_mtp_into_base(
            base_model_path,
            mtp_checkpoint_path,
            output_path,
            key_format,
            draft_config_path=draft_config_path,
        )
    finally:
        export_mtp._load_mtp_source = original


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge trained MTP-HC weights into the Qwen4Exp base model."
    )
    parser.add_argument("--base-model-path", required=True)
    parser.add_argument("--mtp-checkpoint-path", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--draft-config", default=None)
    parser.add_argument(
        "--key-format", default="sglang", choices=["sglang", "hf"],
        help="Native flat layout either way (kept for CLI parity).",
    )
    args = parser.parse_args()
    merge_mtp_hc_into_base(
        args.base_model_path,
        args.mtp_checkpoint_path,
        args.output_path,
        args.key_format,
        args.draft_config,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
