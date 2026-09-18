# Журнал решений

## 2026-09-06 — OptimizePC.exe для отключения защиты и телеметрии

Сделан single-file exe вместо набора .ps1: одинаковый результат на любом ПК без профиля окружения.
Политки пишутся в SOFTWARE\Policies\... — только этот путь гарантированно перечитывается при старте.
Тип старта DiagTrack меняется напрямую в CurrentControlSet, т.к. sc.exe в не-повышенной сессии даёт «Отказано в доступе» даже внутри Start-Process -Verb RunAs.
AllowTelemetry=1, не 0: Microsoft не поддерживает полный off.

## 2026-09-06 — совместимость с Windows Server 2022/2025

Целевой фреймворк — .NET Framework 4.x (уже в поставке Server 2022/2025), PowerShell 5.1 cmdlets без внешних модулей.
Добавлен -NonInteractive для powershell-вызовов: на Server Core нет профиля и ожиданий.
Совместимость проверена повторной сборкой и прогоном: тот же результат (RealTimeProtectionEnabled=False, DiagTrack Disabled).

## 2026-09-06 — возврат после перезагрузки: boot-задача вместо отключения службы

Факт: на сборке 2026 DisableAntiSpyware-политика не переживает boot (Defender включает сам себя),
WinDefend\Start=4 пишется только SYSTEM и даже SYSTEM блокируется minifilter WdFilter; Stop-Service — Access denied.
Решение: exe регистрирует задачу OptimizePC (SYSTEM, AtStartup, +30 с внутри reapply.ps1) —
тампер off + DisableRealtimeMonitoring после каждого boot; реестр политики остаются как бонус на старых сборках.
Ограничение: 30-60 с после включения защиты — окно, пока задача не отработает.
