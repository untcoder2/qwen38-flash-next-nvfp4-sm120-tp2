---
name: reverse-engineering
description: "Analyze binaries, firmware or malware samples with static triage and explicitly isolated dynamic analysis; report behavior, evidence and IOCs."
whenToUse: "Разбери ELF/PE/Mach-O, прошивку, неизвестный executable, malware или CTF rev. Запуск образца — только в разрешённой изолированной среде; разработка PoC известного дефекта — exploit-dev."
---

# Реверс и анализ бинарных артефактов

До использования инструментов прочитай [среду проекта](${KIMI_SKILL_DIR}/../_shared/llm-environment.md); она уточняет место исполнения и не расширяет разрешения.

Результат — объяснение алгоритма/поведения с доказательствами и IOCs. Сначала прочитай правила проекта, область анализа и происхождение образца. Разрешение исследовать файл само по себе не разрешает запустить его на рабочем компьютере или инференс-хосте.

## Статический этап

1. Сохрани исходник, hash, формат, архитектуру, compiler/packer признаки и ограничения инструментов.
2. Метаданные/imports/strings/ресурсы/config: file, readelf/objdump/rabin2 или эквивалент. Поиск по строкам ограничивай вопросом задачи; найденные credentials обезличивай.
3. В Ghidra/rizin/radare2 или доступном decompiler проследи entry point, ключевые функции, сетевые/файловые/процессные операции, crypto/obfuscation и поток данных.
4. Отличай признаки packing/anti-debug/anti-VM от доказанного поведения. Распаковку выполняй на копии; запуск unpacking stub уже относится к динамике.

## Динамический этап

Только по разрешённой задаче в disposable VM/sandbox с известным snapshot и контролируемой сетью. Зафиксируй отсутствие доступа к production, общим credentials/папкам и соседним ресурсам. Обычный SSH на сервер с GPU не создаёт sandbox.

Используй gdb/x64dbg, syscall/API tracing, файловые/процессные/registry snapshots и изолированный capture по необходимости. Не подключай образец к реальным C2 и не создавай persistence или self-propagation. При отсутствии подходящей среды заверши static-этап с точным списком непроверенного.

Для CTF rev восстанови алгоритм/check и проверяемый ответ. Для известного memory-corruption дефекта опиши reachability и mitigations; запрос на отдельный PoC передай в exploit-dev.

## Отчёт

Идентификация, ключевые функции/адреса и data flow, подтверждённые возможности, IOCs (hash/domain/path/mutex/registry), наблюдённая anti-analysis логика, оценка benign/suspicious/malicious и основания. Разделяй static inference и реально наблюдённую динамику. Анализ легитимизирует область разрешения оператора, а не свойства модели.
