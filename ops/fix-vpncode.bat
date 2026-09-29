@echo off
title vpncode wire fix
python D:\llm\ops\fix_vpncode_wire.py
if errorlevel 1 echo FAILED - show this output to the assistant
pause
