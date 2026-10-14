# coding=utf-8
"""specforge_ext — Qwen3.8-Flash-Next MTP training extension for SpecForge.

Import (or call :func:`install`) before any SpecForge entry point so the
``Qwen4ExpMTPDraft`` architecture is in ``DRAFT_REGISTRY`` and the
``mtp_hc`` algorithm is in the builtin algorithm catalog:

    python -m specforge_ext.shim.train_hc -c run.yaml
    python -m specforge_ext.shim.prepare_hc ...

Layout on the training host (see README):

    ~/specforge/specforge_ext/
        __init__.py            <- this file
        qwen4_exp_mtp_draft.py
        configs/qwen4-exp-flash-next-mtp.json
        shim/{__init__,capture_hc,mtp_hc_algorithm,normalizers,prepare_hc,merge_hc,train_hc}.py
        selftest.py

Nothing under SpecForge itself is modified; the hook is the
``builtin_algorithm_registry`` patch below.
"""

from __future__ import annotations

import os
import sys

# Allow running the extension straight from a checkout (no install step):
# make both `specforge_ext` and the sibling draft module importable when this
# directory is not yet copied under ~/specforge.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_INSTALLED = False


def install() -> None:
    """Register the draft architecture and the mtp_hc algorithm.

    Idempotent. Safe to call from any SpecForge entry point (torchrun worker,
    prepare script, merge CLI) before config resolution.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    # 0. Register the fork's ``qwen4_exp`` model type with transformers'
    #    AutoConfig. SpecForge's prepare_hidden_states.main() reads
    #    AutoConfig.from_pretrained() before any SGLangRunner import, and bare
    #    transformers (even with trust_remote_code) rejects the checkpoint;
    #    importing the fork's model module installs CONFIG_MAPPING first.
    try:
        import sglang.srt.models.qwen4_exp  # noqa: F401
    except Exception:
        pass

    # 1. Draft class -> specforge DRAFT_REGISTRY (via @register_draft at import).
    from .. import qwen4_exp_mtp_draft  # noqa: F401

    # 2. Algorithm registration. SpecForge builds its catalog lazily inside
    #    resolve_run()/resolve_offline_capture() through
    #    `from specforge.algorithms.builtin import builtin_algorithm_registry`,
    #    so patching that module attribute (before the first resolution) is
    #    enough to inject a custom strategy without touching upstream files.
    import specforge.algorithms.builtin as _builtin
    from specforge.algorithms.registry import AlgorithmRegistry

    from . import mtp_hc_algorithm

    if _builtin.builtin_algorithm_registry.__name__ != "installed_algorithm_registry":
        _base_builtin = _builtin.builtin_algorithm_registry

        def installed_algorithm_registry() -> AlgorithmRegistry:
            return _base_builtin().with_registration(mtp_hc_algorithm.create_registration())

        installed_algorithm_registry.__name__ = "installed_algorithm_registry"
        _builtin.builtin_algorithm_registry = installed_algorithm_registry

    _INSTALLED = True


__all__ = ["install"]
