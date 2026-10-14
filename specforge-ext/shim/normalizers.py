# coding=utf-8
"""Offline feature schema for HC-stream MTP capture.

Mirrors ``specforge.algorithms.common.hidden_states_data`` MTP helpers with the
tensor renamed to ``target_hc_hidden_states`` (width ``hc_count*hidden_size``).
Kept dependency-light (torch only at import; specforge internals are imported
lazily inside the factories) so it is importable in a CPU-only environment.
"""

from __future__ import annotations

from functools import partial
from typing import Optional

import torch

MTP_HC_NORMALIZER_ID = "mtp_hc_offline_v1"
HC_FEATURE = "target_hc_hidden_states"
REQUIRED_FEATURES = ("input_ids", "loss_mask", HC_FEATURE)


def _normalize_feature(raw, key: str, max_len: int, *, description: str,
                       expected_width: Optional[int] = None):
    hidden_states = raw[key]
    if hidden_states.dim() == 3:
        if hidden_states.shape[0] != 1:
            raise ValueError(
                f"offline {description} must have shape [seq, width] or "
                f"[1, seq, width], got {tuple(hidden_states.shape)}"
            )
        hidden_states = hidden_states.squeeze(0)
    if hidden_states.dim() != 2:
        raise ValueError(
            f"offline {description} must have shape [seq, width] or "
            f"[1, seq, width], got {tuple(hidden_states.shape)}"
        )
    if expected_width is not None and hidden_states.shape[-1] != expected_width:
        raise ValueError(
            f"offline {description} has width {hidden_states.shape[-1]}, "
            f"expected {expected_width} (hc_count*hidden_size)"
        )
    return hidden_states[:max_len].unsqueeze(0)


def normalize_mtp_hc_offline_sample(raw, max_len: int, feature_dim: Optional[int] = None):
    """One ``.ckpt`` record -> padded-single batch tensors for MTP-HC training."""

    input_ids = raw["input_ids"][:max_len].unsqueeze(0)
    loss_mask = raw["loss_mask"][:max_len].unsqueeze(0)
    hc_states = _normalize_feature(
        raw,
        HC_FEATURE,
        max_len,
        description="MTP-HC target_hc_hidden_states",
        expected_width=feature_dim,
    )
    lengths = {input_ids.shape[1], loss_mask.shape[1], hc_states.shape[1]}
    if len(lengths) != 1:
        raise ValueError(
            "offline MTP-HC features have mismatched sequence lengths after "
            f"truncation: input_ids={input_ids.shape[1]}, "
            f"loss_mask={loss_mask.shape[1]}, {HC_FEATURE}={hc_states.shape[1]}"
        )
    return {"input_ids": input_ids, "loss_mask": loss_mask, HC_FEATURE: hc_states}


def build_mtp_hc_offline_normalizer(max_len, feature_dim=None, **_topology):
    return partial(normalize_mtp_hc_offline_sample, max_len=max_len,
                   feature_dim=feature_dim)


def build_mtp_hc_offline_reader(
    strategy,
    hidden_states_path,
    *,
    run_id,
    ttt_length,
    max_len,
):
    from specforge.runtime.data_plane.offline_reader import OfflineManifestReader

    return OfflineManifestReader(
        hidden_states_path,
        run_id=run_id,
        strategy=strategy,
        feature_keys=REQUIRED_FEATURES,
        target_repr="hidden_state",
        ttt_length=ttt_length,
        max_len=max_len,
    )


def build_mtp_hc_collator():
    from specforge.algorithms.common.collation import pad_and_concatenate_features

    def collate(features):
        return pad_and_concatenate_features(
            features,
            sequence_axes={key: 1 for key in REQUIRED_FEATURES},
            required_keys=REQUIRED_FEATURES,
            optional_keys=(),
        )

    return collate


def materialize_hc_record(input_ids, loss_mask, hc_hidden_states):
    """One persisted sample record, in the deterministic key order the reader wants."""

    return {
        "input_ids": input_ids,
        "loss_mask": loss_mask,
        HC_FEATURE: hc_hidden_states,
    }


__all__ = [
    "HC_FEATURE",
    "MTP_HC_NORMALIZER_ID",
    "REQUIRED_FEATURES",
    "build_mtp_hc_collator",
    "build_mtp_hc_offline_normalizer",
    "build_mtp_hc_offline_reader",
    "materialize_hc_record",
    "normalize_mtp_hc_offline_sample",
]
