# coding=utf-8
"""CPU self-check for the Qwen4Exp MTP draft (no GPU, no server).

Validates, against the 31-key manifest captured from the live
``Qwen3.8-Flash-Next-NVFP4-bf16mtp/model.safetensors.index.json``
(ops/rsh.sh read-only pass, 2026-10-10):

1. config resolution through the draft registry (mirrors SpecForge
   ``load_draft_config_source``);
2. ``state_dict`` ``mtp.*`` keys == manifest keys, names/shapes/dtypes exact;
3. ``native_state_contract()`` (config-derived) == manifest;
4. strict ``load_state_dict`` of a full 31-key random state and of
   ``required_native_state_keys()``-shaped init-from-checkpoint semantics
   (missing key -> failure, shared lm_head tolerated);
5. fixed forward at batch 1x8 with the 10240-wide HC feature, and the
   SpecForge ``OnlineMTPModel`` objective (shift, chunked CE, accuracy) with
   backward; unused-indexer parameters get no gradient.

Run from this directory:
    D:/llm/.venv-sftest/Scripts/python.exe specforge-ext/selftest.py
On the server (with SpecForge installed) the same command works from
~/specforge:  python specforge_ext/selftest.py
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))  # parent of specforge-ext/ for aliases

# ---------------------------------------------------------------------------
# Native head manifest — verbatim (name, dtype, shape) triples from the live
# checkpoint index. Regenerate with:
#   ops/rsh.sh 'python3 -c "...read safetensors headers..."'  (see README)
# ---------------------------------------------------------------------------
BF = "bfloat16"
NATIVE_MANIFEST: dict[str, tuple[str, list[int]]] = {
    "mtp.fc_embedding.weight": (BF, [2560, 2560]),
    "mtp.fc_hidden.weight": (BF, [2560, 2560]),
    "mtp.hyper_connection_mixer.hc_norm.weight": (BF, [10240]),
    "mtp.hyper_connection_mixer.input_mix_weight_down.weight": (BF, [320, 10240]),
    "mtp.hyper_connection_mixer.input_mix_weight_up.weight": (BF, [10240, 320]),
    "mtp.layers.0.attn_hyper_connection.block_inject_weight.weight": (BF, [4, 10240]),
    "mtp.layers.0.attn_hyper_connection.hc_norm.weight": (BF, [10240]),
    "mtp.layers.0.attn_hyper_connection.input_mix_weight_down.weight": (BF, [320, 10240]),
    "mtp.layers.0.attn_hyper_connection.input_mix_weight_up.weight": (BF, [10240, 320]),
    "mtp.layers.0.mlp.experts.down_proj": (BF, [512, 2560, 640]),
    "mtp.layers.0.mlp.experts.gate_up_proj": (BF, [512, 1280, 2560]),
    "mtp.layers.0.mlp.gate.weight": (BF, [512, 2560]),
    "mtp.layers.0.mlp.shared_expert.down_proj.weight": (BF, [2560, 640]),
    "mtp.layers.0.mlp.shared_expert.gate_proj.weight": (BF, [640, 2560]),
    "mtp.layers.0.mlp.shared_expert.up_proj.weight": (BF, [640, 2560]),
    "mtp.layers.0.mlp.shared_expert_gate.weight": (BF, [1, 2560]),
    "mtp.layers.0.mlp_hyper_connection.block_inject_weight.weight": (BF, [4, 10240]),
    "mtp.layers.0.mlp_hyper_connection.hc_norm.weight": (BF, [10240]),
    "mtp.layers.0.mlp_hyper_connection.input_mix_weight_down.weight": (BF, [320, 10240]),
    "mtp.layers.0.mlp_hyper_connection.input_mix_weight_up.weight": (BF, [10240, 320]),
    "mtp.layers.0.self_attn.indexer.index_qk_proj.weight": (BF, [640, 2560]),
    "mtp.layers.0.self_attn.indexer.k_layernorm.weight": (BF, [128]),
    "mtp.layers.0.self_attn.indexer.q_layernorm.weight": (BF, [128]),
    "mtp.layers.0.self_attn.k_norm.weight": (BF, [256]),
    "mtp.layers.0.self_attn.k_proj.weight": (BF, [512, 2560]),
    "mtp.layers.0.self_attn.o_proj.weight": (BF, [2560, 6144]),
    "mtp.layers.0.self_attn.q_norm.weight": (BF, [256]),
    "mtp.layers.0.self_attn.q_proj.weight": (BF, [12288, 2560]),
    "mtp.layers.0.self_attn.v_proj.weight": (BF, [512, 2560]),
    "mtp.pre_fc_norm_embedding.weight": (BF, [2560]),
    "mtp.pre_fc_norm_hidden.weight": (BF, [10240]),
}

_DTYPES = {"bfloat16": torch.bfloat16, "float32": torch.float32}


def _stub_parent(pkg_name: str, directory: Path) -> None:
    """Register an unexecuted package stub whose submodules load from disk."""
    from importlib.machinery import ModuleSpec

    mod = types.ModuleType(pkg_name)
    mod.__spec__ = ModuleSpec(pkg_name, loader=None, is_package=True)
    mod.__spec__.submodule_search_locations = [str(directory)]
    mod.__path__ = [str(directory)]
    sys.modules[pkg_name] = mod


def _ensure_specforge() -> str:
    """Make ``specforge.modeling.draft.registry``/``mtp.base``/``_mask_utils``
    importable: prefer the installed package, else load the real files from the
    read-only ``.upstream`` clone through stub parents (the package __init__
    chain pulls triton/sglang, which must not be needed on CPU)."""
    try:
        importlib.import_module("specforge.modeling.draft.mtp.base")
        importlib.import_module("specforge.modeling._mask_utils")
        return "installed"
    except Exception:
        pass
    upstream = ROOT / ".upstream"
    if not (upstream / "specforge").is_dir():
        raise RuntimeError(
            "specforge is not installed and the .upstream reference clone is "
            "missing (git clone --depth 1 https://github.com/sgl-project/SpecForge "
            f"{upstream})"
        )
    for pkg in ("specforge", "specforge.modeling", "specforge.modeling.draft",
                "specforge.modeling.draft.mtp", "specforge.core"):
        if pkg in sys.modules:
            continue
        _stub_parent(pkg, upstream / pkg.replace(".", "/"))
    return ".upstream"


def _load_extension():
    spec = importlib.util.spec_from_file_location(
        "specforge_ext", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["specforge_ext"] = pkg
    spec.loader.exec_module(pkg)
    draft_mod = importlib.import_module("specforge_ext.qwen4_exp_mtp_draft")
    return draft_mod


def _make_config(draft_mod, config_dict: dict):
    config_dict = dict(config_dict)
    config_dict["tie_word_embeddings"] = False
    config_dict.setdefault("draft_vocab_size", config_dict["vocab_size"])
    config = draft_mod.Qwen4ExpMTPDraft.config_class.from_dict(config_dict)
    config._attn_implementation = "eager"
    return config


def _build_state_dict(contract, *, zeros: bool, seed: int = 0):
    gen = torch.Generator().manual_seed(seed)
    state = {}
    for key, (dtype, shape) in contract.items():
        dtype = dtype if isinstance(dtype, torch.dtype) else _DTYPES[dtype]
        shape = tuple(shape)
        if zeros:
            state[key] = torch.zeros(shape, dtype=dtype)
        else:
            # small-scale normal init; the stacked expert tensors stay cheap
            state[key] = (torch.randn(shape, generator=gen) * 0.02).to(dtype)
    return state


def _check(name: str, ok: bool, detail: str = "") -> int:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return 0 if ok else 1


def main() -> int:
    torch.manual_seed(1234)
    failures = 0
    seam = _ensure_specforge()
    draft_mod = _load_extension()
    Qwen4ExpMTPDraft = draft_mod.Qwen4ExpMTPDraft

    from specforge.modeling.draft.registry import DRAFT_REGISTRY

    failures += _check(
        "registry: Qwen4ExpMTPDraft registered via @register_draft",
        DRAFT_REGISTRY.get("Qwen4ExpMTPDraft") is Qwen4ExpMTPDraft,
        f"(specforge seam: {seam})",
    )

    # ---- full-size draft from the shipped config --------------------------
    with open(ROOT / "configs" / "qwen4-exp-flash-next-mtp.json") as f:
        full_dict = json.load(f)
    config = _make_config(draft_mod, full_dict)

    with torch.device("meta"):  # shape/dtype contract check without 10 GB allocs
        probe = Qwen4ExpMTPDraft(config)
        probe = probe.to(torch.bfloat16)  # AutoDraftModel.from_config does this
        probe_sd = probe.state_dict()
    mtp_keys = {k: v for k, v in probe_sd.items() if k.startswith("mtp.")}

    # The draft additionally owns the shared lm_head (excluded from the
    # required native set, backfilled by the merge — not in the live index).
    expected_model_keys = set(NATIVE_MANIFEST) | {"mtp.lm_head.weight"}
    missing = sorted(expected_model_keys - set(mtp_keys))
    extra = sorted(set(mtp_keys) - expected_model_keys)
    failures += _check(
        "state_dict mtp.* keys == live index 31-key set + shared lm_head",
        not missing and not extra,
        f"missing={missing} extra={extra}" if (missing or extra) else f"{len(mtp_keys)} keys",
    )

    shape_bad = []
    for key, (dtype, shape) in NATIVE_MANIFEST.items():
        tensor = mtp_keys.get(key)
        if tensor is None:
            continue
        if tuple(tensor.shape) != tuple(shape) or tensor.dtype != _DTYPES[dtype]:
            shape_bad.append(
                f"{key}: got {tuple(tensor.shape)}/{tensor.dtype}, "
                f"want {tuple(shape)}/{dtype}"
            )
    failures += _check("state_dict shapes+dtypes == manifest", not shape_bad, "; ".join(shape_bad[:4]))

    contract = probe.native_state_contract()
    contract_bad = [
        f"{k}: got {contract.get(k)}, want {(tuple(_DTYPES[d]), tuple(s))}"
        for k, (d, s) in NATIVE_MANIFEST.items()
        if contract.get(k) != (_DTYPES[d], tuple(s))
    ]
    failures += _check("native_state_contract() == manifest", not contract_bad, "; ".join(contract_bad[:4]))

    required = probe.required_native_state_keys()
    failures += _check(
        "required_native_state_keys() == 31 native keys (shared lm_head excluded)",
        required == set(NATIVE_MANIFEST) and "mtp.lm_head.weight" not in required,
        f"{len(required)} keys",
    )
    failures += _check(
        "allowed_extra_native_state_keys() covers merged-ckpt backfill",
        probe.allowed_extra_native_state_keys()
        == {"mtp.embed_tokens.weight", "mtp.lm_head.weight"},
    )

    del probe, probe_sd, mtp_keys  # free meta tensors before material build

    # Full-size strict load, replicating the provider gate from
    # specforge.algorithms.mtp.providers._init_from_native_mtp (shared
    # embed/lm_head are legitimately absent from the native prefix).
    print("building full-size draft (CPU bf16, ~8 GB resident)...")
    model = Qwen4ExpMTPDraft(_make_config(draft_mod, full_dict)).to(torch.bfloat16)
    native_state = model.native_state_dict()
    failures += _check(
        "native_state_dict() == 31 mtp.* native keys + mtp.lm_head.weight",
        set(native_state) == expected_model_keys,
        f"{len(native_state)} keys",
    )

    def provider_strict_validate(state_dict, model_ref):
        """Same missing/unexpected rules as _init_from_native_mtp."""
        model_keys = set(model_ref.native_state_dict())
        required_keys = set(model_ref.required_native_state_keys())
        extra_keys = set(model_ref.allowed_extra_native_state_keys())
        missing = sorted(required_keys - set(state_dict))
        unexpected = sorted(set(state_dict) - model_keys - extra_keys)
        if missing or unexpected:
            raise RuntimeError(f"missing={missing} unexpected={unexpected}")
        return model_ref.load_state_dict(state_dict, strict=False)

    full_state = _build_state_dict(NATIVE_MANIFEST, zeros=True)
    result = provider_strict_validate(full_state, model)
    failures += _check(
        "provider-style strict load of all 31 native keys (zeros)",
        not result.unexpected_keys
        and set(result.missing_keys) <= {"embed_tokens.weight", "mtp.lm_head.weight"},
        f"missing={sorted(result.missing_keys)}",
    )

    # Missing required key -> provider gate must reject (not silently randomize).
    partial_state = {k: v for k, v in full_state.items() if k != "mtp.fc_hidden.weight"}
    try:
        provider_strict_validate(partial_state, model)
        strict_rejected = False
    except RuntimeError:
        strict_rejected = True
    failures += _check("partial native state rejected by provider gate", strict_rejected)

    # Merged-checkpoint round-trip: backfilled shared keys under mtp.* tolerated.
    merged_state = dict(full_state)
    merged_state["mtp.embed_tokens.weight"] = torch.zeros(
        config.vocab_size, config.hidden_size, dtype=torch.bfloat16
    )
    merged_state["mtp.lm_head.weight"] = torch.zeros(
        config.vocab_size, config.hidden_size, dtype=torch.bfloat16
    )
    try:
        provider_strict_validate(merged_state, model)
        roundtrip_ok = True
    except RuntimeError:
        roundtrip_ok = False
    failures += _check("merged-ckpt extras (mtp.embed_tokens/lm_head) accepted", roundtrip_ok)

    # Shared frozen embed/lm_head, exactly as the algorithm provider does
    # (TargetEmbeddingsAndHead hands over live nn.Parameters).
    embed_w = torch.nn.Parameter(
        torch.randn(config.vocab_size, config.hidden_size, dtype=torch.bfloat16) * 0.02
    )
    head_w = torch.nn.Parameter(
        torch.randn(config.vocab_size, config.hidden_size, dtype=torch.bfloat16) * 0.02
    )
    model.share_target_embeddings(embed_w, lm_head_weight=head_w)
    failures += _check(
        "share_target_embeddings freezes embed_tokens + mtp.lm_head",
        not model.embed_tokens.weight.requires_grad
        and not model.mtp.lm_head.weight.requires_grad,
    )

    # Fixed forward: batch 1x8 tokens, HC feature width hc_count*hidden=10240.
    bsz, seq, hc_dim = 1, 8, config.hc_count * config.hidden_size
    input_ids = torch.randint(0, config.vocab_size, (bsz, seq))
    hc_features = torch.randn(bsz, seq, hc_dim, dtype=torch.bfloat16) * 0.02
    with torch.no_grad():
        hidden = model.forward_hidden(input_ids, hc_features)
        out = model(input_ids, hc_features)
    failures += _check(
        "forward_hidden -> [1, 8, hidden]; forward -> logits [1, 8, vocab]",
        tuple(hidden.shape) == (bsz, seq, config.hidden_size)
        and tuple(out.logits.shape) == (bsz, seq, config.vocab_size),
        f"{tuple(hidden.shape)} / {tuple(out.logits.shape)}, dtype={hidden.dtype}",
    )
    failures += _check("full-size forward is finite", bool(torch.isfinite(out.logits).all()))

    # SpecForge objective seam (shift + chunked CE + accuracy + backward).
    try:
        from specforge.core.mtp import OnlineMTPModel  # pure torch module
    except Exception as exc:  # .upstream stub parents may not cover imports
        OnlineMTPModel = None
        failures += _check("specforge.core.mtp importable", False, repr(exc))
    if OnlineMTPModel is not None:
        loss_mask = torch.ones(bsz, seq, dtype=torch.long)
        wrapper = OnlineMTPModel(model, objective_chunk_size=0)
        loss, corrects, denoms = wrapper(input_ids, hc_features, loss_mask)
        failures += _check(
            "OnlineMTPModel loss is scalar+finite, acc shapes match",
            loss.dim() == 0 and bool(torch.isfinite(loss))
            and corrects[0].shape == (bsz, seq - 1) and denoms[0].shape == (bsz, seq - 1),
            f"loss={loss.item():.4f}",
        )
        loss.backward()
        grads = dict(model.named_parameters())
        failures += _check(
            "backward reaches fc/fusion/attention/moe params",
            all(
                grads[k].grad is not None
                for k in (
                    "mtp.fc_embedding.weight",
                    "mtp.fc_hidden.weight",
                    "mtp.layers.0.self_attn.q_proj.weight",
                    "mtp.layers.0.attn_hyper_connection.input_mix_weight_up.weight",
                    "mtp.layers.0.mlp.experts.gate_up_proj",
                    "mtp.layers.0.mlp.shared_expert_gate.weight",
                )
            ),
        )
        failures += _check(
            "QSA indexer params are carried but receive no gradient (dense-attention training)",
            grads["mtp.layers.0.self_attn.indexer.index_qk_proj.weight"].grad is None
            and grads["mtp.layers.0.self_attn.indexer.index_qk_proj.weight"].shape == (640, 2560),
        )

    # ---- random-weight numeric smoke on a shrunk clone --------------------
    small_dict = dict(full_dict)
    small_dict.update(
        hidden_size=128, hc_lowrank=16, num_experts=8, num_experts_per_tok=2,
        moe_intermediate_size=32,
        shared_expert_intermediate_size=32, num_attention_heads=4,
        num_key_value_heads=2, head_dim=64, vocab_size=6144, draft_vocab_size=6144,
        pad_token_id=6143,
    )
    small_dict["mtp_config"] = dict(full_dict["mtp_config"], feature_dim=4 * 128)
    small = Qwen4ExpMTPDraft(_make_config(draft_mod, small_dict)).to(torch.bfloat16)
    provider_strict_validate(
        _build_state_dict(small.native_state_contract(), zeros=False, seed=7), small
    )
    small.share_target_embeddings(
        torch.nn.Parameter(torch.randn(6144, 128, dtype=torch.bfloat16) * 0.02),
        lm_head_weight=torch.nn.Parameter(
            torch.randn(6144, 128, dtype=torch.bfloat16) * 0.02
        ),
    )
    x = torch.randint(0, 6144, (2, 8))
    h = torch.randn(2, 8, 4 * 128, dtype=torch.bfloat16)

    small.train()
    logits = small(x, h).logits.float()
    labels = torch.randint(0, 6144, (2, 8), dtype=torch.long)
    loss = torch.nn.functional.cross_entropy(logits.reshape(-1, 6144), labels.reshape(-1))
    loss.backward()
    head_grad = small.mtp.lm_head.weight.grad  # shared+frozen -> must stay None
    trainable_grad = small.mtp.layers[0].self_attn.q_proj.weight.grad
    failures += _check(
        "shrunk clone: finite loss with random weights, shared lm_head frozen, "
        "trainable params get gradients",
        bool(torch.isfinite(loss)) and head_grad is None and trainable_grad is not None,
        f"loss={loss.item():.3f} (ln V ~ {torch.tensor(6144.0).log().item():.2f})",
    )

    print("SELFTEST:", "OK" if failures == 0 else f"{failures} FAILURE(S)")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
