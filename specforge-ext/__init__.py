# coding=utf-8
"""specforge_ext — SpecForge extension for the Qwen3.8-Flash-Next (qwen4_exp)
native MTP head.

This package never subclasses into SpecForge's own modules at import time;
call :func:`specforge_ext.shim.install` (the train/prepare/merge entry points
under ``specforge_ext.shim`` already do) before SpecForge resolves a run.
"""
