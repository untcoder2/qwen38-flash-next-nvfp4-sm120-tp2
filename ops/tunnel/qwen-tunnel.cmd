@echo off
rem qwen-tunnel.cmd — постоянный SSH-туннель 127.0.0.1:1234 -> molochko@192.168.2.127:1234 (SGLang).
rem Нужен, т.к. Claude Desktop разрешает http:// gateway только на loopback, а SGLang говорит чистым http.
rem Автозапуск: задача "QwenTunnel1234" (ONLOGON) через qwen-tunnel-hidden.vbs. Перезапуск при обрыве — цикл.
setlocal
set "SSH=C:\Windows\System32\OpenSSH\ssh.exe"
set "KEY=C:\Users\abcd\.ssh\molochko_deploy"
:loop
"%SSH%" -i "%KEY%" -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -N -L 127.0.0.1:1234:127.0.0.1:1234 molochko@192.168.2.127
timeout /t 5 /nobreak >nul
goto loop
