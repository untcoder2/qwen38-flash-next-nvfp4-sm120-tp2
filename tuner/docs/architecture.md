# Архитектура

Один модуль: `Program.cs` → `OptimizePC.exe` (.NET Framework 4.x, csc v4.0.30319).

| Компонент | Ответственность |
|---|---|
| Main | последовательность: политики → старт службы DiagTrack → Set-MpPreference → рестарт/стоп служб |
| SetPolicy | пишет DWORD в HKLM\SOFTWARE\Policies\... (переживает перезагрузку) |
| SetServiceStart | меняет Start в HKLM\SYSTEM\CurrentControlSet\Services\<name> напрямую (обход отказа sc.exe в не-админ сессии) |
| RunPs | хостит powershell -NoProfile с одной командой; timeout 60 c |
| RestartService / StopService | применяют изменения к WinDefend / DiagTrack в текущей сессии |

Поток данных: реестр и службы — единственное состояние; логи только в stdout.
Граница: exe не чинит окружение — при отказе записи печатает FAIL и идёт дальше.
