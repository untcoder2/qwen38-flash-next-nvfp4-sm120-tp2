# coding=utf-8
"""HC capture hook for offline Qwen4-Exp MTP data preparation.

The built-in MTP strategy persists the target's *post-norm* last hidden state
(``LogitsProcessor._get_pruned_states`` output, width ``hidden_size``).  The
Qwen3.8-Flash-Next native head instead consumes the target's raw
**hyper-connection stream** (width ``hc_count * hidden_size = 10240``): at
serving, ``Qwen4ExpForConditionalGeneration.forward`` publishes
``logits_output.hidden_states = hc_hidden_states`` and the draft worker feeds
that into ``forward_batch.spec_info.hidden_states``.  Offline features must
therefore carry the same tensor.

This module swaps the in-process capture logits processor for a variant that
returns the ``Qwen4ExpVLModel.last_hc_hidden_states`` buffer as the stored
"last hidden" feature.  Everything else (packed prefill, pool reuse, padded
row capture) is inherited from ``specforge.offline_capture``.

Server-only: imports sglang at module load. Kept out of the CPU selftest.
"""

from __future__ import annotations

import logging
from typing import Optional

import torch
import torch.nn as nn
from sglang.srt.layers.logits_processor import LogitsProcessor
from sglang.srt.model_executor.forward_batch_info import ForwardMode

from specforge.offline_capture.sglang_backend.capture import (
    OfflineSGLangCaptureBackend,
)
from specforge.offline_capture.sglang_backend.utils import (
    OfflineEagle3LogitsOutput,
    OfflineEagle3LogitsProcessor,
    replaced_logits_processor_forward_for_offline_eagle3,
)

logger = logging.getLogger(__name__)

# Serve-exact runtime fidelity for capture + the fork's publish() contract.
# SpecForge's frozen capture flow predates both, so we wrap the symbols it
# imports, right in its own module namespace, before ``super().build()``.
_FORK_PATCHES_APPLIED = False
_SERVE_JSON_MODEL_OVERRIDES = (
    '{"text_config":{"max_position_embeddings":786432,'
    '"rope_parameters":{"mrope_interleaved":true,"mrope_section":[11,11,10],'
    '"rope_type":"yarn","rope_theta":10000000,"partial_rotary_factor":0.25,'
    '"factor":3.0,"original_max_position_embeddings":262144}}}'
)
# capture-time ServerArgs defaults aligned with the production launch sheet
_SERVE_ALIGN_KWARGS = {
    # ServerArgs.device is normally resolved by the launch-time validators,
    # which the frozen offline path bypasses; torch.device(None) would throw.
    "device": "cuda",
    "json_model_override_args": _SERVE_JSON_MODEL_OVERRIDES,
    "kv_cache_dtype": "fp8_e4m3",
    "page_size": 64,
    "mamba_ssm_dtype": "bfloat16",
    "max_mamba_cache_size": 16,
    "linear_attn_decode_backend": "flashinfer",
    "linear_attn_prefill_backend": "flashinfer",
    "gdn_mtp_cache_mode": "none",
    # the fork's NVFP4 experts refuse the trtllm default; production runs cutlass
    "moe_runner_backend": "flashinfer_cutlass",
    "disable_custom_all_reduce": True,
}


def install_fork_entry_patches() -> None:
    """(1) publish(server_args, role='scheduler') before ModelRunner init — the
    fork's runtime_context asserts config identity; (2) inject serve-aligned
    ServerArgs defaults so captured hidden states match the serving numerics
    (YaRN override, fp8 KV, bf16 SSM, flashinfer GDN) — train/serve skew here
    is exactly the fp8-MTP failure class. Idempotent."""

    global _FORK_PATCHES_APPLIED
    if _FORK_PATCHES_APPLIED:
        return
    import importlib

    import sglang.srt.runtime_context as rc
    cap_mod = importlib.import_module(
        "specforge.offline_capture.sglang_backend.capture"
    )

    orig_sa = cap_mod.ServerArgs

    def serve_aligned_server_args(*a, **kw):
        for key, val in _SERVE_ALIGN_KWARGS.items():
            kw.setdefault(key, val)
        return orig_sa(*a, **kw)

    orig_runner = cap_mod.SGLangRunner

    class PublishingSGLangRunner(orig_runner):
        def __init__(self, *a, **kw):
            sa = kw.get("server_args")
            if sa is None:
                for cand in a:
                    if hasattr(cand, "model_path"):
                        sa = cand
                        break
            if sa is None:
                raise RuntimeError(
                    "publish-patch: server_args not found in SGLangRunner kwargs"
                )
            rc.publish(sa, role="scheduler")
            # MoE layers resolve their runner backend via get_global_server_args(),
            # which publish() alone does not populate.
            try:
                from sglang.srt.server_args import (
                    set_global_server_args_for_scheduler,
                )
                set_global_server_args_for_scheduler(sa)
            except Exception:  # pragma: no cover - older/newer fork layouts
                pass
            # In serving, the scheduler populates the MoE runner-backend global
            # from server_args; the frozen offline runner never reaches that
            # init, leaving AUTO -> FLASHINFER_TRTLLM on SM120 (rejected by the
            # NVFP4 quant method). Mirror the scheduler's entry-time call.
            try:
                from sglang.srt.layers.moe import initialize_moe_config

                initialize_moe_config()
            except Exception:  # pragma: no cover
                pass
            super().__init__(*a, **kw)

    cap_mod.ServerArgs = serve_aligned_server_args
    cap_mod.SGLangRunner = PublishingSGLangRunner
    _FORK_PATCHES_APPLIED = True
    logger.info("fork entry patches installed (publish + serve-aligned ServerArgs)")


class Qwen4ExpHCUnavailable(RuntimeError):
    """Raised when the forward did not publish the hyper-connection stream."""


def _find_hc_source(module: nn.Module) -> nn.Module:
    """Locate the Qwen4Exp submodule that caches the raw HC stream."""

    candidates = [
        name
        for name, submodule in module.named_modules()
        # The VL wrapper stores ``last_hc_hidden_states`` every non-idle
        # forward; the inner Qwen4ExpModel returns the same tensor in-place.
        if hasattr(submodule, "last_hc_hidden_states")
    ]
    if not candidates:
        raise Qwen4ExpHCUnavailable(
            "target model exposes no last_hc_hidden_states buffer; expected a "
            "fork Qwen4ExpVLModel (sglang >= fork pennyroyal-main-sm120-final). "
            "SGLang's LogitsProcessor was still replaced, but offline capture "
            "would store post-norm hidden states instead of the HC stream."
        )
    if len(candidates) > 1:
        logger.warning(
            "multiple HC-buffer modules %s; using the first (alphabetical order)",
            candidates,
        )
    return module.get_submodule(candidates[0])


class OfflineHCLogitsProcessor(nn.Module):
    """Offline capture processor that stores the HC stream as last-hidden.

    Mirrors ``OfflineEagle3LogitsProcessor`` (decode-style pruning so every
    token is kept, no lm-head projection), then swaps the stored tensor for
    the target's per-token hyper-connection stream.
    """

    def __init__(
        self, logits_processor: LogitsProcessor, hc_source: nn.Module, hc_dim: int
    ) -> None:
        super().__init__()
        self.logits_processor = logits_processor
        self.hc_source = hc_source
        self.hc_dim = hc_dim

    def _resolve_hc_states(
        self,
        aux_hidden_states: Optional[torch.Tensor],
        expected_tokens: int,
        hc_dim: int,
    ) -> torch.Tensor:
        hc = getattr(self.hc_source, "last_hc_hidden_states", None)
        if hc is None and aux_hidden_states is not None:
            # Fallback: with an aux-capture setter active on the Qwen4 chain,
            # Qwen3VL.forward unpacks the model's (hidden, hc) tuple, so the
            # HC stream arrives as the aux argument of this call.
            hc = aux_hidden_states
        if hc is None:
            raise Qwen4ExpHCUnavailable(
                "the target forward published no HC stream (last_hc_hidden_states "
                "is None and no aux tensor of the right width was passed)"
            )
        if hc.dim() == 3 and hc.shape[0] == 1:
            hc = hc.squeeze(0)
        if hc.shape[-1] != hc_dim:
            raise Qwen4ExpHCUnavailable(
                f"captured feature width {hc.shape[-1]} != hc_count*hidden_size "
                f"{hc_dim}; wrong tensor captured (check the fork Qwen4ExpModel "
                "mixer wiring)"
            )
        if hc.shape[0] != expected_tokens:
            raise Qwen4ExpHCUnavailable(
                f"captured HC rows {hc.shape[0]} != stored hidden rows "
                f"{expected_tokens}; capture/pruning drifted"
            )
        return hc

    def forward(
        self,
        input_ids,
        hidden_states,
        lm_head,
        logits_metadata,
        aux_hidden_states: Optional[list[torch.Tensor]] = None,
        hidden_states_before_norm: Optional[torch.Tensor] = None,
    ):
        # Offline training needs every token: decode-style pruning keeps all
        # packed tokens (``_get_pruned_states`` is a pass-through there).
        logits_metadata.forward_mode = ForwardMode.DECODE
        out = replaced_logits_processor_forward_for_offline_eagle3(
            self.logits_processor,
            input_ids,
            hidden_states,
            lm_head,
            logits_metadata,
            aux_hidden_states,
            hidden_states_before_norm,
        )
        hc = self._resolve_hc_states(
            out.aux_hidden_states,
            expected_tokens=out.last_hidden_states.shape[0],
            hc_dim=self.hc_dim,
        )
        return OfflineEagle3LogitsOutput(
            logits=None,
            # The generic MTP capture layout discards aux; keep the post-norm
            # states there purely to satisfy the shared capture contract.
            aux_hidden_states=out.aux_hidden_states,
            last_hidden_states=hc,
        )


def make_offline_hc_logits_processor(
    logits_processor: LogitsProcessor,
    hc_source: nn.Module,
    hc_dim: int,
) -> OfflineHCLogitsProcessor:
    return OfflineHCLogitsProcessor(logits_processor, hc_source, hc_dim)


def wrap_offline_hc_logits_processors(module: nn.Module, hc_dim: int) -> None:
    """Replace each capture logits processor with the HC-storing variant.

    Idempotent over SpecForge's ``wrap_offline_eagle3_logits_processors``:
    already-wrapped processors are rebuilt around their inner LogitsProcessor,
    bare LogitsProcessors are wrapped directly.
    """

    hc_source = _find_hc_source(module)
    replacements = []
    for name, submodule in module.named_modules():
        if isinstance(submodule, (OfflineEagle3LogitsProcessor, LogitsProcessor)):
            if not name:
                raise RuntimeError("the target model itself cannot be a logits processor")
            inner = (
                submodule.logits_processor
                if isinstance(submodule, OfflineEagle3LogitsProcessor)
                else submodule
            )
            replacements.append((name, inner))
    if not replacements:
        raise RuntimeError("no SGLang logits processor found on the target model")
    for name, inner in replacements:
        parent_name, _, child_name = name.rpartition(".")
        parent = module.get_submodule(parent_name) if parent_name else module
        setattr(parent, child_name, make_offline_hc_logits_processor(inner, hc_source, hc_dim))


class OfflineHCCaptureBackend(OfflineSGLangCaptureBackend):
    """OfflineSGLangCaptureBackend with the HC-stream capture swap.

    The parent ``build`` freezes the target through the in-process
    ``SGLangRunner`` and wraps logits processors for EAGLE3-style capture; we
    re-wrap them for the HC stream immediately afterwards.  ``hc_dim`` is read
    from the fork model itself (``hc_count * hidden_size``) so a mismatch
    between the draft config and the served target fails at build time.
    """

    @classmethod
    def build(
        cls,
        pretrained_model_name_or_path: str,
        *,
        torch_dtype: Optional[torch.dtype] = None,
        trust_remote_code: bool = False,
        **kwargs,
    ) -> "OfflineHCCaptureBackend":
        install_fork_entry_patches()
        instance = super().build(            pretrained_model_name_or_path,
            torch_dtype=torch_dtype,
            trust_remote_code=trust_remote_code,
            **kwargs,
        )
        model = instance.model_runner.model
        hc_dim = _target_hc_dim(model)
        wrap_offline_hc_logits_processors(model, hc_dim)
        instance.hc_dim = hc_dim
        logger.info(
            "offline capture will store the raw HC stream (width %d) as "
            "target_hc_hidden_states",
            hc_dim,
        )
        return instance

    def set_capture_layers(self, layer_ids=None, *, capture_method="dflash"):
        """No-op the aux-layer hook.

        The fork's Qwen4 chain never returns auxiliary per-layer tensors on
        non-idle forwards (``Qwen4ExpModel.forward`` returns the HC tuple
        instead), and enabling ``capture_aux_hidden_states`` on the outer VL
        class would mis-unpack that tuple. MTP heads do not consume aux
        layers; the generic capture contract only needs a placeholder tensor,
        which the processor already fills with the post-norm states.
        """
        if layer_ids not in (None, [], ()) and capture_method == "dflash":
            logger.info(
                "mtp_hc offline capture ignores aux layer_ids=%s (HC stream is "
                "captured through the logits-processor swap)",
                layer_ids,
            )
        return None


def _target_hc_dim(model: nn.Module) -> int:
    """hc_count * hidden_size as the served target model reports it."""

    for _, submodule in model.named_modules():
        config = getattr(submodule, "config", None)
        hc_count = getattr(config, "hc_count", None)
        hidden_size = getattr(config, "hidden_size", None)
        if hc_count and hidden_size:
            return int(hc_count) * int(hidden_size)
    raise Qwen4ExpHCUnavailable(
        "could not derive hc_count*hidden_size from the served target config"
    )


__all__ = [
    "OfflineHCCaptureBackend",
    "OfflineHCLogitsProcessor",
    "Qwen4ExpHCUnavailable",
    "wrap_offline_hc_logits_processors",
]
