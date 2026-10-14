# Разовый регистратор автозапуска туннеля QwenTunnel (HKCU Run, без админа)
$val = 'wscript.exe "D:\llm\ops\tunnel\qwen-tunnel-hidden.vbs"'
New-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'QwenTunnel' -Value $val -PropertyType String -Force | Out-Null
Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'QwenTunnel' | Select-Object -ExpandProperty QwenTunnel
