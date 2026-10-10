@echo off
rem Kimi Token Ops — почасовой ходлес-пуш wire-статистики + удалённый refresh.
rem Создаётся задачей планировщика «KimiDashboardPush». Браузер НЕ открывает.
setlocal
chcp 65001 >/dev/null
set "PYTHONIOENCODING=utf-8"
set "LOG=D:\llm\tmp\dash_push.log"
set "PY=C:\Users\abcd\AppData\Local\Programs\Python\Python312\python.exe"
set "KEY=%USERPROFILE%\.ssh\molochko_deploy"
echo. >> "%LOG%"
echo ===== %date% %time% push_wire ===== >> "%LOG%"
"%PY%" D:\llm\ops\push_wire.py >> "%LOG%" 2>&1
echo ===== remote refresh ===== >> "%LOG%"
ssh -i "%KEY%" -o BatchMode=yes -o ConnectTimeout=8 molochko@192.168.2.127 "cd /home/molochko/dashboard && /usr/bin/python3 refresh.py >> refresh.log 2>&1" >> "%LOG%" 2>&1
echo ===== done rc=%ERRORLEVEL% %date% %time% ===== >> "%LOG%"
endlocal
