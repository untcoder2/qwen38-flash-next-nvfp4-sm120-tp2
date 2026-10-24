---
name: mobile-appsec
description: "Assess an authorized APK or IPA using static analysis and, when available and permitted, isolated device testing with evidence and fixes."
whenToUse: "Анализ APK/IPA, mobile pentest, декомпиляция приложения или разрешённая проверка pinning/runtime. Для глубокой native-библиотеки — reverse-engineering; отсутствие устройства не мешает статическому анализу."
---

# Безопасность Android и iOS

До использования инструментов прочитай [среду проекта](${KIMI_SKILL_DIR}/../_shared/llm-environment.md); она уточняет место исполнения и не расширяет разрешения.

Прочитай правила проекта и зафиксируй приложение/версию, тестовые аккаунты, scope и режим: static или dynamic. Результат — MASVS/MSTG-сопоставленные находки с доказательством и исправлением, а не каталог теоретических слабостей.

## Статический анализ

- Зафиксируй hash и происхождение APK/IPA. Распаковывай в отдельный каталог без перезаписи исходника и чужих файлов.
- Android: manifest, exported components, permissions, debuggable/backup/cleartext, intent/deep links; jadx для Java/Kotlin, apktool для ресурсов/smali.
- Проследи auth, crypto, storage, WebView bridges, IPC/providers и входы из deep links. Секреты ищи с выводом имён файлов и обезличенных фрагментов.
- iOS: Info.plist/ATS/URL schemes, entitlements, Mach-O, provisioning, Keychain/data protection; доступные decompiler/class metadata инструменты.
- Native libs анализируй статически или передай в reverse-engineering при достаточной задаче. MobSF — дополнительный источник кандидатов, не обязательный запуск или установка.

## Динамический режим

Нужны явно выбранный тестовый device/emulator и разрешённые методы. Сначала проверь доступность устройства и версии adb/Frida/objection, затем поддерживаемые параметры. Не переносись автоматически на личный/production телефон и не запускай неизвестное приложение на общем боевом хосте.

В scope: перехват тестового HTTPS, минимальные Frida hooks/pinning bypass, тесты storage/IPC/WebView/deep links и утечек logcat. Не выводи данные реальных пользователей и credentials.

Если устройства/прав/инструмента нет, выполни доступный static-анализ и перечисли конкретные dynamic-проверки, которые остались непроверенными. Не утверждай, что runtime-поведение проверено по одному manifest. Отсутствие pinning или отдельной защитной опции оценивай в контексте модели угроз, а не автоматически как подтверждённую уязвимость.

## Отчёт

Для находки: app/hash/version, static или dynamic источник, file/component, вход → нарушение → impact, предусловия, категория MASVS/MSTG и минимальный fix. Рекомендации и неопределённости держи отдельно от доказанных дефектов.
