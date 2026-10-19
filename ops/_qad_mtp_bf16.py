#!/usr/bin/env python3
"""QAD MTP head -> BF16 fused shard for the prod draft loader.

Input (downloaded by ops/_dl_mtp_ranges.sh + headers rput next to them), in
<dl_dir>:
  hdr_00001.bin, hdr_00015.bin  safetensors headers of the FP4-Local checkpoint
  s1_scale2.bin   F32 weight_scale_2 scalars   (shard 00001, 3652..9796)
  s1_scales.bin   F8_E4M3 per-16 block scales  (shard 00001, ~2.704e9..)
  s1_weights.bin  U8 packed E2M1 codes         (shard 00001, ~3.741e9..)
  s15_mtp.bin     29 raw BF16 MTP tensors      (shard 00015, ~2.759e9..)

Output: a valid safetensors file with exactly the 31 mtp.* keys and BF16
dtype of the prod model-mtp-bf16.safetensors (same names, same shapes, same
order), where the two fused expert stacks
  mtp.layers.0.mlp.experts.gate_up_proj [512,1280,2560]   (gate rows, then up)
  mtp.layers.0.mlp.experts.down_proj    [512,2560,640]
are produced by NVFP4 dequantization:
  w_bf16 = RNE_bf16( f32(E2M1[code]) * ( f32(E4M3(scale)) * weight_scale_2 ) )
low nibble = even element, sign = bit 3; block scales are LINEAR [N, K/16]
(no swizzle in ModelOpt serialized checkpoints). weight_scale_2 is a decode
multiplier (not inverted). 29 non-MoE tensors are copied byte-for-byte.

Pure numpy, streamed per expert, peak RAM ~ tens of MB.

Usage: python3 _qad_mtp_bf16.py <dl_dir> <out_shard> [prod_ref] [fp8_ref]
Exit 0 only if BUILD_OK (keys/shapes/nan/amax/size checks all pass).
"""
import json
import random
import struct
import sys
import os

import numpy as np

if not __debug__:
    raise SystemExit("run without -O: contract checks are asserts by design")

E2M1_LUT = np.array([0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0,
                     -0.0, -0.5, -1.0, -1.5, -2.0, -3.0, -4.0, -6.0],
                    dtype=np.float32)
SPANS = {  # blob name -> (file, global start of the downloaded range)
    "s1_scale2": (3652, 9796),
    "s1_scales": (2703630440, 2860916840),
    "s1_weights": (3741211240, 4999502440),
    "s15_mtp": (2759389912, 2940526808),
}
STACK_GU = "mtp.layers.0.mlp.experts.gate_up_proj"
STACK_DN = "mtp.layers.0.mlp.experts.down_proj"
EXP, H, MOE = 512, 2560, 640


def e4m3_to_f32(b: np.ndarray) -> np.ndarray:
    b = b.astype(np.uint8)
    s = np.where((b >> 7) != 0, -1.0, 1.0).astype(np.float32)
    m = (b & 7).astype(np.float32)
    e = ((b >> 3) & 15).astype(np.int32)
    val = np.where(e > 0, (1.0 + m / 8.0) * np.ldexp(np.ones_like(m), e - 7),
                   m * np.float32(2.0 ** -9))
    val = np.where((e == 15) & (m == 7), np.float32("nan"), val)
    return (s * val).astype(np.float32)


def f32_to_bf16_rne(x: np.ndarray) -> np.ndarray:
    u = x.astype(np.float32).view(np.uint32)
    u = u + np.uint32(0x7FFF) + ((u >> np.uint32(16)) & np.uint32(1))
    return (u >> np.uint32(16)).astype(np.uint16)


def bf16_bits_to_f32(u16: np.ndarray) -> np.ndarray:
    return (u16.astype(np.uint32) << np.uint32(16)).view(np.float32)


def load_header(path):
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        return json.loads(f.read(n)), 8 + n


class Reader:
    """Reads by RELATIVE data_offsets from the named range blob.

    Offsets are per-file, so the span must be given explicitly by the caller:
    shard-00015 relative offsets numerically collide with shard-00001 spans
    (both ~2.7e9), and guessing by range would silently cross files.
    """

    def __init__(self, dl_dir):
        self.f = {name: open(os.path.join(dl_dir, name + ".bin"), "rb")
                  for name in SPANS}

    def read(self, span, offs, dtype=None):
        s, e = offs
        bs, be = SPANS[span]
        if not (bs <= s and e <= be):
            raise RuntimeError(f"offset {s}-{e} outside {span} {bs}-{be}")
        f = self.f[span]
        f.seek(s - bs)
        buf = f.read(e - s)
        if len(buf) != e - s:
            raise RuntimeError(f"short read {span} {s}-{e}")
        return np.frombuffer(buf, dtype or np.uint8)


def dequant(reader, hdr1, idx, proj, n_out, n_in):
    """(expert, proj) -> float32 [n_out, n_in] via U8 packed + E4M3 + f32 gs."""
    base = f"mtp.layers.0.mlp.experts.{idx}.{proj}"
    assert hdr1[base + ".weight"]["shape"] == [n_out, n_in // 2], base
    assert hdr1[base + ".weight_scale"]["shape"] == [n_out, n_in // 16], base
    assert hdr1[base + ".weight_scale"]["dtype"] == "F8_E4M3", base
    assert hdr1[base + ".weight_scale_2"]["dtype"] == "F32", base
    packed = reader.read("s1_weights", hdr1[base + ".weight"]["data_offsets"]).reshape(n_out, n_in // 2)
    scale = reader.read("s1_scales", hdr1[base + ".weight_scale"]["data_offsets"]).reshape(n_out, n_in // 16)
    gs = reader.read("s1_scale2", hdr1[base + ".weight_scale_2"]["data_offsets"]).view(np.float32)[0]
    codes = np.empty((n_out, n_in), dtype=np.uint8)
    codes[:, 0::2] = packed & 15
    codes[:, 1::2] = packed >> 4
    t = (e4m3_to_f32(scale) * gs).astype(np.float32)
    return (E2M1_LUT[codes].reshape(n_out, n_in // 16, 16)
            * t[:, :, None]).reshape(n_out, n_in)


def main():
    dl = sys.argv[1] if len(sys.argv) > 1 else "/home/molochko/vllm-models/_fp4local_dl"
    out_path = sys.argv[2] if len(sys.argv) > 2 else dl + "/model-mtp-qad-bf16.safetensors"
    prod_ref = sys.argv[3] if len(sys.argv) > 3 else (
        "/home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp/model-mtp-bf16.safetensors")
    fp8_ref = sys.argv[4] if len(sys.argv) > 4 else (
        "/home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-official/model-fp8-mtp-ple.safetensors")

    hdr1, _ = load_header(os.path.join(dl, "hdr_00001.bin"))
    hdr15, _ = load_header(os.path.join(dl, "hdr_00015.bin"))
    prod_h, prod_prefix = load_header(prod_ref)
    mtp1 = {k: v for k, v in hdr1.items() if k.startswith("mtp.")}
    mtp15 = {k: v for k, v in hdr15.items() if k.startswith("mtp.")}
    order = [k for k in prod_h if k.startswith("mtp.")]
    assert len(mtp1) == 4608 and len(mtp15) == 29 and len(order) == 31

    # shape/name contract against the prod head
    for k in order:
        if k in mtp15:
            assert mtp15[k]["shape"] == prod_h[k]["shape"], k
            assert mtp15[k]["dtype"] == prod_h[k]["dtype"] == "BF16", k
    assert prod_h[STACK_GU]["shape"] == [EXP, 2 * MOE, H]
    assert prod_h[STACK_DN]["shape"] == [EXP, H, MOE]

    rd = Reader(dl)
    rng = random.Random(20261019)

    plan = []
    for k in order:
        shp = prod_h[k]["shape"]
        plan.append((k, shp, int(np.prod(shp)) * 2))
    offs, cur = {}, 0
    for k, _, nb in plan:
        offs[k] = (cur, cur + nb)
        cur += nb
    header = {k: {"dtype": "BF16", "shape": s, "data_offsets": list(offs[k])}
              for k, s, _ in plan}
    header["__metadata__"] = {"format": "pt"}
    hb = json.dumps(header, separators=(",", ":")).encode()

    stats = {"nan": 0, "absmax": 0.0, "amax_pass": 0, "amax_fail": 0, "amax_zero": 0}

    def feed(x):  # x float32 slice pre-cast
        stats["nan"] += int(np.isnan(x).sum())
        stats["absmax"] = max(stats["absmax"], float(np.nanmax(np.abs(x))) if x.size else 0.0)

    with open(out_path, "wb") as f:
        f.write(struct.pack("<Q", len(hb)))
        f.write(hb)
        for k, shp, nb in plan:
            if k == STACK_GU:
                for e in range(EXP):
                    for proj in ("gate_proj", "up_proj"):
                        w = dequant(rd, mtp1, e, proj, MOE, H)
                        feed(w)
                        f.write(f32_to_bf16_rne(w).tobytes())
            elif k == STACK_DN:
                for e in range(EXP):
                    w = dequant(rd, mtp1, e, "down_proj", H, MOE)
                    feed(w)
                    f.write(f32_to_bf16_rne(w).tobytes())
            else:
                raw = rd.read("s15_mtp", mtp15[k]["data_offsets"])
                assert raw.size == nb
                feed(bf16_bits_to_f32(raw.view(np.uint16)))
                f.write(raw.tobytes())
            assert f.tell() - (8 + len(hb)) == offs[k][1], k

    size = os.path.getsize(out_path)

    # read-back spot check: fresh file slices must equal fresh dequant
    rb_hdr, rb_prefix = load_header(out_path)
    assert sorted(k for k in rb_hdr if k.startswith("mtp.")) == sorted(order)
    with open(out_path, "rb") as f:
        f.seek(rb_prefix + offs[STACK_GU][0])
        got = np.frombuffer(f.read(MOE * H * 2), np.uint16).reshape(MOE, H)
    want = f32_to_bf16_rne(dequant(rd, mtp1, 0, "gate_proj", MOE, H))
    assert got.shape == want.shape == (MOE, H)
    assert (got == want).all(), "read-back mismatch e0 gate"

    # amax self-check vs stored scales on random experts
    for e in rng.sample(range(EXP), 30):
        for proj, (no, ni) in (("gate_proj", (MOE, H)), ("down_proj", (H, MOE))):
            w = dequant(rd, mtp1, e, proj, no, ni)
            bidx = f"mtp.layers.0.mlp.experts.{e}.{proj}"
            sc = e4m3_to_f32(rd.read("s1_scales", mtp1[bidx + ".weight_scale"]["data_offsets"]).reshape(no, ni // 16))
            gs = rd.read("s1_scale2", mtp1[bidx + ".weight_scale_2"]["data_offsets"]).view(np.float32)[0]
            meas = np.abs(w.reshape(no, ni // 16, 16)).max(-1)
            want = sc * np.float32(6.0) * np.float32(gs)
            zero = want == 0
            stats["amax_zero"] += int(zero.sum())
            rel = np.abs(meas[~zero] - want[~zero]) / np.maximum(want[~zero], 1e-30)
            stats["amax_pass"] += int((rel <= 0.35).sum())
            stats["amax_fail"] += int((rel > 0.35).sum())

    # lineage correlations vs current RadixArk head and official FP8 head
    corr = {}
    for e in (0, 257, 511):
        new = dequant(rd, mtp1, e, "gate_proj", MOE, H)
        # slice of the e-th expert gate rows from the prod fused stack, via
        # the PROD header's own offsets (its writer may have padded)
        gu_s = prod_h[STACK_GU]["data_offsets"][0]
        with open(prod_ref, "rb") as f:
            f.seek(prod_prefix + gu_s + e * 2 * MOE * H * 2)
            old = bf16_bits_to_f32(np.frombuffer(f.read(MOE * H * 2), np.uint16).reshape(MOE, H))
        corr[f"gate_e{e}_vs_radixark"] = round(float(np.corrcoef(
            new.ravel().astype(np.float64), old.ravel().astype(np.float64))[0, 1]), 4)
    h8, pfx8 = load_header(fp8_ref)
    b8 = "mtp.layers.0.mlp.experts.0.gate_proj"
    k8, ks8 = b8 + ".weight", b8 + ".weight_scale_inv"
    if k8 not in h8 or ks8 not in h8:
        raise SystemExit(f"fp8 lineage keys missing — official ref changed? {k8}")
    with open(fp8_ref, "rb") as f:
        f.seek(pfx8 + h8[k8]["data_offsets"][0])
        w8 = np.frombuffer(f.read(h8[k8]["data_offsets"][1] - h8[k8]["data_offsets"][0]),
                           np.uint8).reshape(MOE, H)
        f.seek(pfx8 + h8[ks8]["data_offsets"][0])
        si = bf16_bits_to_f32(np.frombuffer(f.read(5 * 20 * 2), np.uint16).reshape(5, 20))
    si = np.repeat(np.repeat(si, 128, 0), 128, 1).astype(np.float32)
    old8 = (e4m3_to_f32(w8) * si).astype(np.float32)
    new0 = dequant(rd, mtp1, 0, "gate_proj", MOE, H)
    corr["gate_e0_vs_official_fp8"] = round(float(np.corrcoef(
        new0.ravel().astype(np.float64), old8.ravel().astype(np.float64))[0, 1]), 4)
    assert len(corr) == 4, corr  # 3x radixark + 1x official fp8

    ok = (stats["nan"] == 0 and stats["amax_fail"] == 0 and stats["absmax"] < 60
          and abs(size - os.path.getsize(prod_ref)) < 2_000_000
          and len(corr) >= 4 and all(np.isfinite(v) and v > 0.3 for v in corr.values()))
    report = {"out": out_path, "size": size, "prod_ref_size": os.path.getsize(prod_ref),
              "keys": len(plan), "nan": stats["nan"],
              "absmax": round(stats["absmax"], 4),
              "amax_pass": stats["amax_pass"], "amax_fail": stats["amax_fail"],
              "amax_zero": stats["amax_zero"], "corr": corr, "ok": ok}
    with open(os.path.join(dl, "qad_mtp_report.json"), "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))

    print("BUILD_" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
