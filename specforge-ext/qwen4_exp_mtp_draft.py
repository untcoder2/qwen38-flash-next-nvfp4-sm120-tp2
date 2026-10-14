# coding=utf-8
"""Multi-Token Prediction (MTP) draft model for Qwen3.8-Flash-Next (qwen4_exp).

Training-side mirror of the serving head
``sglang.srt.models.qwen4_exp_mtp.Qwen4ExpForCausalLMMTP`` (fork branch
``pennyroyal-main-sm120-final``).  Architecture for ``hc_count > 1``:

  1. Fuse token embeddings with the target's *hyper-connection* stream:
     ``fc_embedding(norm(emb))`` is broadcast-added onto every one of the
     ``hc_count`` decoder streams of ``norm(hc_states)`` projected through
     ``fc_hidden`` (serving ``_fuse_residual_linear_shared``).  There is no
     single ``fc = Linear(2*H, H)`` in this family; that variant only serves
     ``hc_count <= 1``.
  2. Run one ``full_attention`` decoder layer over the ``hc_count*H`` stream,
     with per-branch gated-residual hyper-connections (mix/combine),
     Qwen3-Next-style gated attention (q_proj emits query+sigmoid gate per
     head, GemmaRMSNorm on q/k, partial NeoX RoPE) and a Qwen3.5-style MoE
     (512 routed experts, top-10, softmax-renormalized, plus one sigmoid
     gated shared expert).
  3. Reduce the stream through the final ``hyper_connection_mixer`` to ``H``
     and compute logits with a shared lm_head.

Weight key layout matches the live native head in the target checkpoint
(``Qwen3.8-Flash-Next-NVFP4-bf16mtp``), verified 2026-10-10 against its
``model.safetensors.index.json`` — exactly 31 ``mtp.*`` keys, all BF16:

    mtp.pre_fc_norm_embedding.weight   [2560]
    mtp.pre_fc_norm_hidden.weight      [10240]
    mtp.fc_embedding.weight            [2560, 2560]
    mtp.fc_hidden.weight               [2560, 2560]
    mtp.hyper_connection_mixer.{hc_norm,input_mix_weight_down,input_mix_weight_up}.weight
    mtp.layers.0.{attn,mlp}_hyper_connection.{block_inject_weight,hc_norm,input_mix_weight_down,input_mix_weight_up}.weight
    mtp.layers.0.self_attn.{q,k,v,o}_proj.weight  (q_proj [12288,2560] = 24 heads x (256 query + 256 gate))
    mtp.layers.0.self_attn.{q,k}_norm.weight      [256]
    mtp.layers.0.self_attn.indexer.{index_qk_proj,q_layernorm,k_layernorm}.weight
    mtp.layers.0.mlp.gate.weight                   [512, 2560]
    mtp.layers.0.mlp.experts.gate_up_proj          [512, 1280, 2560]  (bare param, no .weight)
    mtp.layers.0.mlp.experts.down_proj             [512, 2560, 640]   (bare param, no .weight)
    mtp.layers.0.mlp.shared_expert.{gate,up,down}_proj.weight
    mtp.layers.0.mlp.shared_expert_gate.weight     [1, 2560]

``embed_tokens`` and ``lm_head`` are shared (frozen) from the target by the
algorithm provider, so they are excluded from the required native key set via
``mtp_config.share_lm_head``.

Training deviation from serving (documented): the QSA indexer weights are
loaded (native round-trip fidelity) but the training forward attends densely
over the causal prefix — top-k index selection is a serving-time sparsification
that reuses the target-aligned selection at draft-extend and carries no
supervision signal in offline head fine-tuning.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

import torch
from torch import nn
from torch.nn import functional as F
from transformers.configuration_utils import PretrainedConfig
from transformers.modeling_outputs import CausalLMOutputWithPast

from specforge.modeling._mask_utils import prepare_decoder_attention_mask
from specforge.modeling.draft.mtp.base import MTPDraftModel
from specforge.modeling.draft.registry import register_draft


class GemmaRMSNorm(nn.Module):
    """RMSNorm with the Gemma ``x * (1 + weight)`` convention.

    Matches ``sglang.srt.layers.layernorm.GemmaRMSNorm`` and the checkpoint
    (SpecForge's ``Qwen3_5RMSNorm`` is the same object under another name).
    """

    def __init__(self, dim: int, eps: float = 1e-6) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.zeros(dim))
        self.variance_epsilon = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        input_dtype = x.dtype
        x = x.float()
        variance = x.pow(2).mean(-1, keepdim=True)
        x = x * torch.rsqrt(variance + self.variance_epsilon)
        return (x * (1.0 + self.weight.float())).to(input_dtype)


class GroupedGemmaRMSNorm(GemmaRMSNorm):
    """Gemma RMSNorm computed per ``group_size`` slice (HC branch-wise norm).

    Mirrors ``sglang.srt.layers.hyperconnection.GroupedGemmaRMSNorm`` with
    ``group_size=hidden_size`` and ``hc_per_branch_norm=True``: each of the
    ``hc_count`` streams of the flat ``hc_count*H`` residual is normalized
    with its own variance and its own weight slice.
    """

    def __init__(self, dim: int, group_size: int, eps: float = 1e-6) -> None:
        super().__init__(dim, eps=eps)
        if dim % group_size != 0:
            raise ValueError(f"dim {dim} must be divisible by group_size {group_size}")
        self.group_size = group_size

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        input_dtype = x.dtype
        x = x.float()
        grouped = x.reshape(*x.shape[:-1], x.shape[-1] // self.group_size, self.group_size)
        variance = grouped.pow(2).mean(-1, keepdim=True)
        x = (grouped * torch.rsqrt(variance + self.variance_epsilon)).flatten(-2)
        return (x * (1.0 + self.weight.float())).to(input_dtype)


class GatedResidual(nn.Module):
    """Pure-torch mirror of ``sglang.srt.layers.hyperconnection.GatedResidual``.

    Only the non-JIT math path is mirrored: the gated low-rank mix and the
    sigmoid-inject combine over ``hc_count`` parallel hidden streams.
    ``use_combine=False`` matches the model-level final mixer (which only
    reduces the stream); per-layer mixers use both halves.
    """

    def __init__(
        self,
        hidden_size: int,
        hc_count: int,
        hc_lowrank: int,
        eps: float = 1e-6,
        use_mix: bool = True,
        use_combine: bool = True,
    ) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.hc_count = hc_count
        self.hc_dim = hc_count * hidden_size
        # Qwen4Exp builds HC params in bf16 (params_dtype=torch.bfloat16).
        self.params_dtype = torch.bfloat16
        # hc_per_branch_norm=True -> grouped norm over the flat stream.
        self.hc_norm = GroupedGemmaRMSNorm(self.hc_dim, group_size=hidden_size, eps=eps)
        if use_mix:
            self.input_mix_weight_down = nn.Linear(self.hc_dim, hc_lowrank, bias=False)
            self.input_mix_weight_up = nn.Linear(hc_lowrank, self.hc_dim, bias=False)
        if use_combine:
            self.block_inject_weight = nn.Linear(self.hc_dim, hc_count, bias=False)

    def mix(self, hyper_input: torch.Tensor) -> Tuple[torch.Tensor, tuple]:
        assert hyper_input.shape[-1] == self.hc_dim
        normed = self.hc_norm(hyper_input)
        weights = F.linear(normed, self.input_mix_weight_down.weight) / self.hc_count
        weights = F.linear(F.silu(weights), self.input_mix_weight_up.weight)
        weights = torch.sigmoid(weights)
        mixed = (
            weights.unflatten(-1, (self.hc_count, self.hidden_size))
            * normed.unflatten(-1, (self.hc_count, self.hidden_size))
        ).mean(dim=-2)
        return mixed.to(self.params_dtype), (hyper_input, normed)

    def combine(self, block_output: torch.Tensor, residuals: tuple) -> torch.Tensor:
        hyper_input, normed = residuals
        assert block_output.shape[-1] == self.hidden_size
        inject = 2 * torch.sigmoid(
            F.linear(normed, self.block_inject_weight.weight) / self.hc_count
        )
        streams = hyper_input.unflatten(-1, (self.hc_count, self.hidden_size))
        updated = streams + block_output.unsqueeze(-2) * inject.unsqueeze(-1)
        return updated.flatten(-2).to(self.params_dtype)


class PartialRotaryEmbedding(nn.Module):
    """NeoX-style partial rotary: rotates ``head_dim * partial_rotary_factor`` dims."""

    inv_freq: torch.Tensor

    def __init__(self, head_dim: int, rope_theta: float, partial_rotary_factor: float):
        super().__init__()
        self.rotary_dim = int(head_dim * partial_rotary_factor)
        dim = self.rotary_dim
        inv_freq = 1.0 / (rope_theta ** (torch.arange(0, dim, 2, dtype=torch.float) / dim))
        self.register_buffer("inv_freq", inv_freq, persistent=False)

    @torch.no_grad()
    def forward(self, x, position_ids):
        inv_freq_expanded = (
            self.inv_freq[None, :, None].float().expand(position_ids.shape[0], -1, 1)
        )
        position_ids_expanded = position_ids[:, None, :].float()
        freqs = (inv_freq_expanded @ position_ids_expanded).transpose(1, 2)
        emb = torch.cat((freqs, freqs), dim=-1)
        return emb.cos().to(x.dtype), emb.sin().to(x.dtype)


def _rotate_half(x: torch.Tensor) -> torch.Tensor:
    x1, x2 = x.chunk(2, dim=-1)
    return torch.cat((-x2, x1), dim=-1)


def _apply_rope(
    q: torch.Tensor,
    k: torch.Tensor,
    cos: torch.Tensor,
    sin: torch.Tensor,
    rotary_dim: int,
) -> Tuple[torch.Tensor, torch.Tensor]:
    # cos/sin carry the full rotary_dim; q/k are [B, H, S, head_dim].
    cos = cos.unsqueeze(1)
    sin = sin.unsqueeze(1)
    q_rot, q_pass = q[..., :rotary_dim], q[..., rotary_dim:]
    k_rot, k_pass = k[..., :rotary_dim], k[..., rotary_dim:]
    q = torch.cat([q_rot * cos + _rotate_half(q_rot) * sin, q_pass], dim=-1)
    k = torch.cat([k_rot * cos + _rotate_half(k_rot) * sin, k_pass], dim=-1)
    return q, k


class Qwen4ExpQSAIndexer(nn.Module):
    """Qwen QSA indexer — native weight holder.

    Serving uses this to sparsify attention with a learned top-k selection
    (see ``sglang.srt.layers.attention.qsa.qsa_indexer.QSAIndexer``).  The
    offline training forward attends densely, so the module is never invoked;
    it exists so the 31 native keys round-trip through strict-load and export.
    """

    def __init__(self, config: PretrainedConfig):
        super().__init__()
        self.index_n_heads = int(config.indexer_n_heads)
        self.index_kv_heads = int(config.indexer_kv_heads)
        self.index_head_dim = int(config.indexer_head_dim)
        self.index_qk_proj = nn.Linear(
            config.hidden_size,
            (self.index_n_heads + self.index_kv_heads) * self.index_head_dim,
            bias=False,
        )
        self.q_layernorm = GemmaRMSNorm(self.index_head_dim, eps=config.rms_norm_eps)
        self.k_layernorm = GemmaRMSNorm(self.index_head_dim, eps=config.rms_norm_eps)


class Qwen4ExpMTPAttention(nn.Module):
    """Gated full attention mirroring the serving MTP layer's self_attn.

    ``q_proj`` emits ``num_heads * head_dim * 2`` as per-head
    ``[query(head_dim) | gate(head_dim)]`` pairs (serving ``attn_output_gate``
    path in ``Qwen3_5AttentionDecoderLayer``); q/k are Gemma-RMS-normalized
    per head, partially rotated (NeoX), then the attention output is multiplied
    by ``sigmoid(gate)`` before ``o_proj``.  GQA via
    ``num_key_value_heads``.
    """

    def __init__(self, config: PretrainedConfig):
        super().__init__()
        self.config = config
        self.hidden_size = config.hidden_size
        self.head_dim = config.head_dim
        self.num_heads = config.num_attention_heads
        self.num_kv_heads = config.num_key_value_heads
        self.num_kv_groups = self.num_heads // self.num_kv_heads
        self.scaling = self.head_dim**-0.5
        self.rotary_dim = int(
            self.head_dim * getattr(config, "partial_rotary_factor", 1.0)
        )

        self.q_proj = nn.Linear(
            self.hidden_size, self.num_heads * self.head_dim * 2, bias=config.attention_bias
        )
        self.k_proj = nn.Linear(
            self.hidden_size, self.num_kv_heads * self.head_dim, bias=config.attention_bias
        )
        self.v_proj = nn.Linear(
            self.hidden_size, self.num_kv_heads * self.head_dim, bias=config.attention_bias
        )
        self.o_proj = nn.Linear(
            self.num_heads * self.head_dim, self.hidden_size, bias=config.attention_bias
        )
        self.q_norm = GemmaRMSNorm(self.head_dim, eps=config.rms_norm_eps)
        self.k_norm = GemmaRMSNorm(self.head_dim, eps=config.rms_norm_eps)
        self.indexer = Qwen4ExpQSAIndexer(config)

    def forward(
        self,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor],
        cos: torch.Tensor,
        sin: torch.Tensor,
    ) -> torch.Tensor:
        bsz, q_len, _ = hidden_states.shape
        # Per-head deinterleave of query/gate, matching serving's
        # view(num_heads, -1) + torch.chunk(2, dim=-1).
        q_gate = self.q_proj(hidden_states).view(bsz, q_len, self.num_heads, -1)
        query_states, gate = torch.chunk(q_gate, 2, dim=-1)
        query_states = query_states.transpose(1, 2)  # [B, H, S, D]
        gate = gate.transpose(1, 2)  # [B, H, S, D], per-head sigmoid gate
        key_states = (
            self.k_proj(hidden_states)
            .view(bsz, q_len, self.num_kv_heads, self.head_dim)
            .transpose(1, 2)
        )
        value_states = (
            self.v_proj(hidden_states)
            .view(bsz, q_len, self.num_kv_heads, self.head_dim)
            .transpose(1, 2)
        )

        query_states = self.q_norm(query_states)
        key_states = self.k_norm(key_states)
        query_states, key_states = _apply_rope(
            query_states, key_states, cos, sin, self.rotary_dim
        )

        if self.num_kv_groups > 1:
            key_states = key_states.repeat_interleave(self.num_kv_groups, dim=1)
            value_states = value_states.repeat_interleave(self.num_kv_groups, dim=1)

        attn_weights = torch.matmul(query_states, key_states.transpose(2, 3)) * self.scaling
        if attention_mask is not None:
            attn_weights = attn_weights + attention_mask
        attn_weights = F.softmax(attn_weights, dim=-1, dtype=torch.float32).to(
            query_states.dtype
        )
        if self.training and self.config.attention_dropout > 0:
            attn_weights = F.dropout(
                attn_weights, p=self.config.attention_dropout, training=True
            )
        attn_output = torch.matmul(attn_weights, value_states)
        attn_output = attn_output * torch.sigmoid(gate)
        attn_output = attn_output.transpose(1, 2).reshape(bsz, q_len, -1)
        return self.o_proj(attn_output)


class Qwen4ExpMTPMoE(nn.Module):
    """Qwen3.5-style sparse MoE with fused stacked expert weights.

    Router: softmax over all experts, top-``num_experts_per_tok`` selection,
    weights renormalized when ``norm_topk_prob`` (serving
    ``RoutingMethodType.RenormalizeNaive``).  One shared expert MLP gated by
    ``sigmoid(shared_expert_gate(x))`` is added to the routed sum.  Expert
    tensors mirror the checkpoint's stacked parameters
    ``experts.gate_up_proj [E, 2*I, H]`` and ``experts.down_proj [E, H, I]``
    (gate half first, then up — the FusedMoE w13 layout).
    """

    def __init__(self, config: PretrainedConfig):
        super().__init__()
        self.num_experts = config.num_experts
        self.top_k = config.num_experts_per_tok
        self.norm_topk_prob = config.norm_topk_prob
        self.gate = nn.Linear(config.hidden_size, self.num_experts, bias=False)
        self.experts = nn.Module()  # namespace only; weights are stacked params
        std = math.sqrt(2.0 / (config.moe_intermediate_size + config.hidden_size))
        self.experts.gate_up_proj = nn.Parameter(
            torch.empty(self.num_experts, 2 * config.moe_intermediate_size, config.hidden_size)
        )
        self.experts.down_proj = nn.Parameter(
            torch.empty(self.num_experts, config.hidden_size, config.moe_intermediate_size)
        )
        if self.experts.gate_up_proj.device.type != "meta":
            nn.init.normal_(self.experts.gate_up_proj, 0.0, std)
            nn.init.normal_(self.experts.down_proj, 0.0, std)
        inter = config.shared_expert_intermediate_size
        self.shared_expert_gate = nn.Linear(config.hidden_size, 1, bias=False)
        self.shared_expert = nn.Module()
        self.shared_expert.gate_proj = nn.Linear(config.hidden_size, inter, bias=False)
        self.shared_expert.up_proj = nn.Linear(config.hidden_size, inter, bias=False)
        self.shared_expert.down_proj = nn.Linear(inter, config.hidden_size, bias=False)

    def _shared_mlp(self, x: torch.Tensor) -> torch.Tensor:
        return self.shared_expert.down_proj(
            F.silu(self.shared_expert.gate_proj(x)) * self.shared_expert.up_proj(x)
        )

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        shape = hidden_states.shape
        x = hidden_states.view(-1, shape[-1])
        tokens = x.shape[0]

        shared = self._shared_mlp(x) * torch.sigmoid(self.shared_expert_gate(x))

        router_logits = self.gate(x)
        # fp32 softmax over all experts (HF Qwen3-MoE / serving TopK parity),
        # then top-k on the probabilities and optional renormalization.
        weights = F.softmax(router_logits, dim=-1, dtype=torch.float32)
        weights, topk = torch.topk(weights.to(x.dtype), self.top_k, dim=-1)
        if self.norm_topk_prob:
            weights = weights / weights.sum(dim=-1, keepdim=True)
        weights = weights.view(-1, 1)

        flat_topk = topk.view(-1)
        out = torch.zeros_like(x)
        token_idx = torch.arange(tokens, device=x.device).repeat_interleave(self.top_k)
        # One grouped GEMM per expert that received tokens (top-k pairs are
        # bucketed by arg sort over the flat expert ids).
        order = torch.argsort(flat_topk, stable=True)
        sorted_experts = flat_topk[order]
        counts = torch.bincount(sorted_experts, minlength=self.num_experts)
        bounds = torch.cumsum(counts, dim=0)
        for e in torch.unique(sorted_experts).tolist():
            start = int(bounds[e - 1]) if e > 0 else 0
            stop = int(bounds[e])
            rows = order[start:stop]
            tokens_e = x[token_idx[rows]]
            gate_up = tokens_e @ self.experts.gate_up_proj[e].t()
            gate, up = gate_up.chunk(2, dim=-1)
            out.index_add_(
                0,
                token_idx[rows],
                ((F.silu(gate) * up) @ self.experts.down_proj[e].t()) * weights[rows],
            )
        return (out + shared).view(shape)


class Qwen4ExpMTPDecoderLayer(nn.Module):
    """One full-attention HC layer over the ``hc_count*H`` stream.

    Mirrors ``Qwen4ExpAttentionDecoderLayer`` minus TP/DP collectives, PLE and
    QSA sparsification: attn_hyper_connection mixes the flat stream down to a
    branch input, attention writes back with a gated inject, then
    mlp_hyper_connection does the same around the MoE.
    """

    def __init__(self, config: PretrainedConfig):
        super().__init__()
        self.self_attn = Qwen4ExpMTPAttention(config)
        self.mlp = Qwen4ExpMTPMoE(config)
        self.attn_hyper_connection = GatedResidual(
            config.hidden_size, config.hc_count, config.hc_lowrank,
            eps=config.rms_norm_eps,
        )
        self.mlp_hyper_connection = GatedResidual(
            config.hidden_size, config.hc_count, config.hc_lowrank,
            eps=config.rms_norm_eps,
        )

    def forward(
        self,
        hidden_states: torch.Tensor,  # [B, S, hc_count*H]
        attention_mask: Optional[torch.Tensor],
        cos: torch.Tensor,
        sin: torch.Tensor,
    ) -> torch.Tensor:
        mixed, residuals = self.attn_hyper_connection.mix(hidden_states)
        mixed = self.self_attn(mixed, attention_mask, cos, sin)
        hidden_states = self.attn_hyper_connection.combine(mixed, residuals)

        mixed, residuals = self.mlp_hyper_connection.mix(hidden_states)
        mixed = self.mlp(mixed)
        return self.mlp_hyper_connection.combine(mixed, residuals)


class Qwen4ExpMTPModel(nn.Module):
    """The trainable MTP body, namespaced under ``mtp.`` to match the native
    checkpoint keys consumed by the fork's ``Qwen4ExpForCausalLMMTP``."""

    def __init__(self, config: PretrainedConfig):
        super().__init__()
        hc_dim = config.hc_count * config.hidden_size

        self.pre_fc_norm_embedding = GemmaRMSNorm(
            config.hidden_size, eps=config.rms_norm_eps
        )
        self.pre_fc_norm_hidden = GemmaRMSNorm(hc_dim, eps=config.rms_norm_eps)
        self.fc_embedding = nn.Linear(config.hidden_size, config.hidden_size, bias=False)
        self.fc_hidden = nn.Linear(config.hidden_size, config.hidden_size, bias=False)

        self.layers = nn.ModuleList([Qwen4ExpMTPDecoderLayer(config)])
        # Model-level reduction mixer: mix only (serving use_combine=False).
        self.hyper_connection_mixer = GatedResidual(
            config.hidden_size, config.hc_count, config.hc_lowrank,
            eps=config.rms_norm_eps, use_combine=False,
        )
        self.rotary_emb = PartialRotaryEmbedding(
            config.head_dim, config.rope_theta, config.partial_rotary_factor
        )
        self.lm_head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)

    def fuse_input(self, input_embeds: torch.Tensor, hc_states: torch.Tensor) -> torch.Tensor:
        """Serving ``_fuse_residual_linear_shared``, operand-for-operand.

        ``input_embeds`` [B,S,H]; ``hc_states`` [B,S,hc_count*H] (the target's
        raw hyper-connection stream, pre final mixer).  The fused result keeps
        the flat stream width ``hc_count*H``; the Gemma norms do their
        variance/weight math in fp32 internally, as in serving.
        """
        emb = self.fc_embedding(self.pre_fc_norm_embedding(input_embeds))
        normed = self.pre_fc_norm_hidden(hc_states)
        streams = normed.unflatten(-1, (self.hyper_connection_mixer.hc_count, emb.shape[-1]))
        return (emb.unsqueeze(-2) + self.fc_hidden(streams)).flatten(-2)

    def forward(
        self,
        inputs_embeds: torch.Tensor,
        hidden_states: torch.Tensor,  # target hc stream [B, S, hc_count*H]
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        hidden_states = self.fuse_input(inputs_embeds, hidden_states).to(
            self.hyper_connection_mixer.params_dtype
        )
        bsz, seq_len, _ = hidden_states.shape
        if position_ids is None:
            position_ids = (
                torch.arange(seq_len, dtype=torch.long, device=hidden_states.device)
                .unsqueeze(0)
                .expand(bsz, -1)
            )
        cos, sin = self.rotary_emb(hidden_states, position_ids)

        mask = prepare_decoder_attention_mask(
            attention_mask, hidden_states, bsz, seq_len
        )
        if mask is not None:
            mask = mask.to(hidden_states.dtype)
        for layer in self.layers:
            hidden_states = layer(hidden_states, mask, cos, sin)

        reduced, _ = self.hyper_connection_mixer.mix(hidden_states)
        return reduced


@register_draft
class Qwen4ExpMTPDraft(MTPDraftModel):
    """SpecForge training draft for the Qwen3.8-Flash-Next native MTP head.

    The embed table and lm_head are the target's, loaded and frozen by the
    algorithm provider (``share_target_embeddings``).  Trainable tensors all
    live under ``mtp.*`` with the native serving names, so
    ``scripts/merge_mtp_to_base``-style exports round-trip into the fork's
    ``Qwen4ExpForCausalLMMTP`` loader unchanged.
    """

    config_class = PretrainedConfig
    _no_split_modules = ["Qwen4ExpMTPDecoderLayer"]

    def __init__(self, config: PretrainedConfig) -> None:
        super().__init__()
        self.config = config
        self.embed_tokens = nn.Embedding(
            config.vocab_size, config.hidden_size,
            padding_idx=getattr(config, "pad_token_id", None),
        )
        self.mtp = Qwen4ExpMTPModel(config)

    def get_input_embeddings(self) -> nn.Module:
        return self.embed_tokens

    def set_input_embeddings(self, value: nn.Module) -> None:
        self.embed_tokens = value

    def get_output_embeddings(self) -> nn.Module:
        return self.mtp.lm_head

    def set_output_embeddings(self, value: nn.Module) -> None:
        self.mtp.lm_head = value

    def native_state_contract(self) -> dict[str, tuple[torch.dtype, tuple[int, ...]]]:
        """Expected dtype/shape for every native ``mtp.*`` key, per config.

        Derived from the live ``Qwen3.8-Flash-Next-NVFP4-bf16mtp`` index (31
        BF16 tensors); used by selftest and by any loader wanting a
        dtype-contracted strict load independent of torch's lazy parameter
        materialization.
        """
        c = self.config
        h, hc, lr = c.hidden_size, c.hc_count, c.hc_lowrank
        hd = c.head_dim
        nh, nkv = c.num_attention_heads, c.num_key_value_heads
        e, mi, si = c.num_experts, c.moe_intermediate_size, c.shared_expert_intermediate_size
        in_h = (c.indexer_n_heads + c.indexer_kv_heads) * c.indexer_head_dim
        bd = f"{self.NATIVE_KEY_PREFIX}layers.0."
        contract: dict[str, tuple[torch.dtype, tuple[int, ...]]] = {
            f"{self.NATIVE_KEY_PREFIX}pre_fc_norm_embedding.weight": (torch.bfloat16, (h,)),
            f"{self.NATIVE_KEY_PREFIX}pre_fc_norm_hidden.weight": (torch.bfloat16, (hc * h,)),
            f"{self.NATIVE_KEY_PREFIX}fc_embedding.weight": (torch.bfloat16, (h, h)),
            f"{self.NATIVE_KEY_PREFIX}fc_hidden.weight": (torch.bfloat16, (h, h)),
            f"{self.NATIVE_KEY_PREFIX}hyper_connection_mixer.hc_norm.weight": (torch.bfloat16, (hc * h,)),
            f"{self.NATIVE_KEY_PREFIX}hyper_connection_mixer.input_mix_weight_down.weight": (torch.bfloat16, (lr, hc * h)),
            f"{self.NATIVE_KEY_PREFIX}hyper_connection_mixer.input_mix_weight_up.weight": (torch.bfloat16, (hc * h, lr)),
        }
        for branch in ("attn_hyper_connection", "mlp_hyper_connection"):
            k = bd + branch
            contract[f"{k}.hc_norm.weight"] = (torch.bfloat16, (hc * h,))
            contract[f"{k}.input_mix_weight_down.weight"] = (torch.bfloat16, (lr, hc * h))
            contract[f"{k}.input_mix_weight_up.weight"] = (torch.bfloat16, (hc * h, lr))
            contract[f"{k}.block_inject_weight.weight"] = (torch.bfloat16, (hc, hc * h))
        attn = bd + "self_attn."
        contract[attn + "q_proj.weight"] = (torch.bfloat16, (nh * hd * 2, h))
        contract[attn + "k_proj.weight"] = (torch.bfloat16, (nkv * hd, h))
        contract[attn + "v_proj.weight"] = (torch.bfloat16, (nkv * hd, h))
        contract[attn + "o_proj.weight"] = (torch.bfloat16, (h, nh * hd))
        contract[attn + "q_norm.weight"] = (torch.bfloat16, (hd,))
        contract[attn + "k_norm.weight"] = (torch.bfloat16, (hd,))
        idx = attn + "indexer."
        contract[idx + "index_qk_proj.weight"] = (torch.bfloat16, (in_h, h))
        contract[idx + "q_layernorm.weight"] = (torch.bfloat16, (c.indexer_head_dim,))
        contract[idx + "k_layernorm.weight"] = (torch.bfloat16, (c.indexer_head_dim,))
        mlp = bd + "mlp."
        contract[mlp + "gate.weight"] = (torch.bfloat16, (e, h))
        contract[mlp + "experts.gate_up_proj"] = (torch.bfloat16, (e, 2 * mi, h))
        contract[mlp + "experts.down_proj"] = (torch.bfloat16, (e, h, mi))
        contract[mlp + "shared_expert.gate_proj.weight"] = (torch.bfloat16, (si, h))
        contract[mlp + "shared_expert.up_proj.weight"] = (torch.bfloat16, (si, h))
        contract[mlp + "shared_expert.down_proj.weight"] = (torch.bfloat16, (h, si))
        contract[mlp + "shared_expert_gate.weight"] = (torch.bfloat16, (1, h))
        return contract

    def forward_hidden(
        self,
        input_ids: torch.Tensor,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Draft backbone up to (excluding) lm_head. Returns [B, S, hidden]."""
        return self.mtp(
            inputs_embeds=self.embed_tokens(input_ids),
            hidden_states=hidden_states,
            attention_mask=attention_mask,
            position_ids=position_ids,
        )

    def forward(
        self,
        input_ids: torch.Tensor,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
        **kwargs,
    ) -> CausalLMOutputWithPast:
        draft_hidden = self.forward_hidden(
            input_ids=input_ids,
            hidden_states=hidden_states,
            attention_mask=attention_mask,
            position_ids=position_ids,
        )
        return CausalLMOutputWithPast(logits=self.mtp.lm_head(draft_hidden))


__all__ = ["Qwen4ExpMTPDraft", "GemmaRMSNorm", "GatedResidual", "Qwen4ExpMTPModel"]
