# coding=utf-8
"""``mtp_hc`` algorithm registration — MTP fine-tuning over the HC feature.

A near-copy of ``specforge.algorithms.mtp.providers`` with three deltas:

* draft architecture ``Qwen4ExpMTPDraft`` (this repo, ``qwen4_exp_mtp_draft.py``);
* the persisted/required target feature is ``target_hc_hidden_states``
  (``hc_count*hidden_size`` wide, captured by ``shim.capture_hc``) instead of
  ``target_last_hidden_states``;
* the training step consumes that key (``MTPHCTrainStrategy``).

Everything else (native init strictness, shared frozen embed/lm_head, the
chunked CE objective in ``specforge.core.mtp.OnlineMTPModel``) is delegated to
the built-in mtp provider functions so behavior stays in lockstep upstream.
"""

from __future__ import annotations

from functools import partial

from specforge.algorithms.common.defaults import (
    empty_options,
    no_missing_checkpoint_keys,
    one_loss_token,
    online_needs_input_tools,
)
from specforge.algorithms.common.providers import (
    AlgorithmProviders,
    DraftConfigProvider,
    ModelProvider,
    OfflineCaptureLayout,
    OfflineDataProvider,
    ServerCaptureLayout,
    ServerStreamingProvider,
    StepProvider,
    make_registration,
)
from specforge.algorithms.contracts import (
    AlgorithmCapabilities,
    AlgorithmSpec,
    DraftRequirement,
    FeatureContract,
    FeatureMode,
    OfflineStorageContract,
)

ALGORITHM_NAME = "mtp_hc"
DRAFT_ARCHITECTURE = "Qwen4ExpMTPDraft"

# The MTP head consumes only the HC stream; the id list is a placeholder so the
# capture contract keeps a non-empty layer list (see shim capture note).
_CAPTURE_LAYER_IDS = [1]


def _mtp_hc_strategy_class():
    from specforge.training.strategies.base import MTPTrainStrategy

    from .normalizers import HC_FEATURE, REQUIRED_FEATURES

    class MTPHCTrainStrategy(MTPTrainStrategy):
        """MTP objective supervised by the target HC stream (width hc_count*H).

        Same loss as ``MTPTrainStrategy`` (``OnlineMTPModel`` shift + chunked
        CE) with the HC feature substituted for the post-norm last hidden.
        """

        name = ALGORITHM_NAME
        required_features = set(REQUIRED_FEATURES)

        def forward_loss(self, batch, ctx=None):
            self.validate_batch(batch)
            t = batch.tensors
            device = self._device()
            loss, corrects, denoms = self.mtp_model(
                input_ids=t["input_ids"].to(device, non_blocking=True),
                hidden_states=t[HC_FEATURE].to(device, non_blocking=True),
                loss_mask=t["loss_mask"].to(device, non_blocking=True),
            )
            from specforge.training.strategies.base import StepOutput

            correct_sum = corrects[0].sum()
            denom_sum = denoms[0].sum()
            return StepOutput(
                loss=loss,
                metrics={
                    "accuracy": (correct_sum / denom_sum.clamp_min(1)).detach(),
                    "accuracy_denom": denom_sum.detach(),
                },
                ratio_metrics={"accuracy": (correct_sum, denom_sum)},
                loss_terms=(loss * denom_sum, denom_sum),
            )

    return MTPHCTrainStrategy


def build_step(wrapped_model, *, target_head=None, **_options):
    del target_head
    return _mtp_hc_strategy_class()(wrapped_model)


def resume_contract(config, draft_model, training_model):
    from specforge.algorithms.mtp.providers import resume_contract as mtp_resume

    contract = dict(mtp_resume(config, draft_model, training_model))
    contract["mtp_hc_feature"] = "target_hc_hidden_states"
    contract["mtp_hc_feature_dim"] = int(
        (getattr(draft_model.config, "mtp_config", None) or {}).get("feature_dim", 0)
    )
    return contract


def build_draft(cfg, draft_config):
    """Mirror of ``specforge.algorithms.mtp.providers.build_draft``.

    ``_init_from_native_mtp`` matches the 31 native ``mtp.*`` keys of the
    Qwen4Exp head against the draft's ``required_native_state_keys()`` (strict:
    missing or unexpected keys abort), then the target embed/lm_head are
    shared and frozen.
    """

    import torch

    from specforge.algorithms.mtp.providers import (
        _init_from_native_mtp,
        _share_target_embeddings,
    )
    from specforge.modeling.auto import AutoDraftModel
    from specforge.training.model_loading import warm_start_draft_model
    from specforge.utils import get_local_device

    torch_dtype = getattr(torch, cfg.model.torch_dtype)
    draft_config._attn_implementation = cfg.training.attention_backend
    draft_model = AutoDraftModel.from_config(draft_config, torch_dtype=torch_dtype)

    _init_from_native_mtp(cfg, draft_model)
    _share_target_embeddings(cfg, draft_model, torch_dtype)

    if cfg.model.draft_checkpoint_path:
        warm_start_draft_model(
            draft_model,
            cfg.model.draft_checkpoint_path,
            draft_config=draft_config,
            strategy=cfg.training.strategy,
            cache_dir=cfg.model.cache_dir,
            trust_remote_code=cfg.model.trust_remote_code,
        )
    return draft_model.to(device=get_local_device(), dtype=torch_dtype)


def build_training_model(config, draft_model, draft_config, target_config, tokenizer):
    from specforge.algorithms.model_providers import AlgorithmModelParts
    from specforge.core.mtp import OnlineMTPModel

    return AlgorithmModelParts(
        model=OnlineMTPModel(
            draft_model=draft_model,
            objective_chunk_size=config.training.mtp_objective_chunk_size,
        ),
        capture_layers=None,
    )


def resolve_capture_layers(config, draft_config, target_config):
    # Never consumed by the HC capture path (shim.capture_hc no-ops the aux
    # setter); kept to satisfy the composition-root layer validation.
    return list(_CAPTURE_LAYER_IDS)


_REQUIRED = {"input_ids", "loss_mask", "target_hc_hidden_states"}


def algorithm_spec() -> AlgorithmSpec:
    return AlgorithmSpec(
        name=ALGORITHM_NAME,
        draft=DraftRequirement(
            compatible_architectures={DRAFT_ARCHITECTURE},
            default_architecture=DRAFT_ARCHITECTURE,
        ),
        feature_contracts=(
            FeatureContract(
                mode=FeatureMode.OFFLINE,
                modality="text",
                required_tensors=_REQUIRED,
                allowed_target_representations={"hidden_state"},
                default_target_representation="hidden_state",
                storage=OfflineStorageContract(
                    format="specforge_hidden_states_v1",
                    required_tensors=_REQUIRED,
                    normalizer="mtp_hc_offline_v1",
                ),
            ),
            FeatureContract(
                mode=FeatureMode.STREAMING,
                modality="text",
                required_tensors=_REQUIRED,
                allowed_target_representations={"hidden_state"},
                default_target_representation="hidden_state",
            ),
        ),
        capabilities=AlgorithmCapabilities(
            # flex_attention is declared solely so the offline-capture resolve
            # (which builds training={"strategy": ...} and inherits the schema
            # default backend) validates; capture never runs draft attention.
            # Real training pins attention_backend=sdpa in the run yaml.
            attention_backends={"eager", "sdpa", "flex_attention"},
        ),
    )


def algorithm_providers() -> AlgorithmProviders:
    from .normalizers import (
        MTP_HC_NORMALIZER_ID,
        build_mtp_hc_collator,
        build_mtp_hc_offline_normalizer,
        build_mtp_hc_offline_reader,
    )

    return AlgorithmProviders(
        algorithm_name=ALGORITHM_NAME,
        step=StepProvider(
            build=build_step,
            options=empty_options,
            resume_contract=resume_contract,
            allowed_missing_checkpoint_keys=no_missing_checkpoint_keys,
            uses_external_target_head=False,
        ),
        model=ModelProvider(
            draft_config=DraftConfigProvider(
                architecture=DRAFT_ARCHITECTURE,
                expected_auto_map_model="qwen4_exp_mtp_draft.Qwen4ExpMTPDraft",
            ),
            build_draft=build_draft,
            build_training_model=build_training_model,
            resolve_capture_layers=resolve_capture_layers,
            minimum_loss_tokens=one_loss_token,
            needs_input_tools=online_needs_input_tools,
            default_dataloader_num_workers=8,
        ),
        offline=(
            OfflineDataProvider(
                modality="text",
                normalizer_id=MTP_HC_NORMALIZER_ID,
                capture_layout=OfflineCaptureLayout(
                    capture_method="dflash",
                    aux_feature=None,
                    last_hidden_feature="target_hc_hidden_states",
                    passthrough=(
                        ("input_ids", "input_ids"),
                        ("loss_mask", "loss_mask"),
                    ),
                ),
                build_reader=partial(build_mtp_hc_offline_reader, ALGORITHM_NAME),
                build_normalizer=build_mtp_hc_offline_normalizer,
                build_collator=build_mtp_hc_collator,
            ),
        ),
        server_streaming=(
            ServerStreamingProvider(
                modality="text",
                capture_method="dflash",
                target_representation="hidden_state",
                layout=ServerCaptureLayout(
                    aux_feature="hidden_states",
                    last_hidden_feature="target_hc_hidden_states",
                    passthrough=(
                        ("input_ids", "input_ids", ()),
                        ("loss_mask", "loss_mask", ()),
                    ),
                ),
                build_collator=build_mtp_hc_collator,
            ),
        ),
    )


def create_registration():
    return make_registration(algorithm_spec(), algorithm_providers())


__all__ = [
    "ALGORITHM_NAME",
    "DRAFT_ARCHITECTURE",
    "algorithm_providers",
    "algorithm_spec",
    "build_draft",
    "build_step",
    "create_registration",
]
