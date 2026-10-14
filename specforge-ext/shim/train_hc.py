# coding=utf-8
"""Train the Qwen4Exp MTP head through ``specforge.cli`` with mtp_hc installed.

    python -m specforge_ext.shim.train_hc train -c run-qwen4-exp-mtp-hc.yaml
    # (multi-GPU: torchrun --nproc-per-node=N -m specforge_ext.shim.train_hc train -c ...)

Everything after ``install()`` is SpecForge's own CLI: config validation,
launch planning, trainer assembly.
"""

from __future__ import annotations

import sys

from specforge_ext.shim import install


def main(argv: list[str] | None = None) -> int:
    install()
    from specforge.cli import main as specforge_main

    return specforge_main(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
