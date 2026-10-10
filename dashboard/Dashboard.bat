@echo off
cd /d "%~dp0"
python ..\ops\push_wire.py
ssh -i %USERPROFILE%\.ssh\molochko_deploy -o BatchMode=yes molochko@192.168.2.127 "cd /home/molochko/dashboard && python3 refresh.py > /dev/null 2>&1"
start "" http://192.168.2.127:8090/
