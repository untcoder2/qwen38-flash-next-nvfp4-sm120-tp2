# OptimizePC

Мини-утилита (.exe) для массовой настройки Windows: отключает лишнее в Defender и телеметрии. Один запуск — все правки применяются.

## Запуск

```
OptimizePC.exe
```

Manifest требует прав администратора (автоповышение через UAC).

Работает на Windows 10/11 и Windows Server 2022/2025 (включая Server Core и Nano):
используются только встроенные PowerShell 5.1 cmdlets и реестр; .NET Framework
4.8 уже есть в этих ОС. На Nano Service не нужен — службы там и так под LocalSystem.

## Что делает

| Правка | Где | Эффект |
|---|---|---|
| DisableAntiSpyware=1, DisableAntiMalware=1 | HKLM\SOFTWARE\Policies\Microsoft\Windows Defender | Defender не включает реальную защиту |
| AllowTelemetry=1 | HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection | телеметрия — минимальный уровень |
| Start=4 | HKLM\SYSTEM\CurrentControlSet\Services\DiagTrack | служба DiagTrack отключена |
| Set-MpPreference -DisableRealtimeMonitoring $true | через powershell | реальная защита off сразу |
| задача OptimizePC | Планировщик, NT AUTHORITY\SYSTEM, AtStartup+30с | тампер-протекция Windows возвращает защиту после ребута — задача reapлиает отключение (reapply.ps1 в C:\ProgramData\OptimizePC) |

Порядок: сначала гасится TamperProtection, иначе Windows откатывает всё после перезагрузки.
Прямая запись `WinDefend\Start=4` блокируется (DACL службы) — поэтому надёжного off без
задачи-возврата нет. Задача пересоздаётся при каждом запуске exe (идемпотентно).

## Проверка

```powershell
Get-MpComputerStatus | Select-Object RealTimeProtectionEnabled
(Get-Service DiagTrack).StartType   # Disabled
```

Архитектура и журнал решений — в `docs/`.
