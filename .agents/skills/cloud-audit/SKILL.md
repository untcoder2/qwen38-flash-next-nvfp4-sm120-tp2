---
name: cloud-audit
description: "Audit authorized cloud accounts, deployed container platforms and IaC for exposure, excessive privilege and security misconfiguration."
whenToUse: "Аудит AWS/Azure/GCP, Kubernetes, работающей Docker-платформы или Terraform/CloudFormation. Dockerfile и конфиги обычного приложения без платформенного контекста — security-review."
---

# Аудит облачной и контейнерной платформы

До использования инструментов прочитай [среду проекта](${KIMI_SKILL_DIR}/../_shared/llm-environment.md); она уточняет место исполнения и не расширяет разрешения.

Результат — проверенные misconfigurations, пути повышения прав и конкретная remediation. Прочитай правила проекта. Выбери режим: статический IaC или read-only аудит развёрнутой платформы. Последний требует известного account/subscription/project/cluster/context; не переключай их молча.

## Ограничения

Используй уже данную область и least-privilege audit credentials. Подтверди фактическую identity/context перед сетевыми вызовами. Не извлекай реальные данные или значения secrets. Запрос на аудит не разрешает менять IAM, сеть, workloads, создавать privileged pod или исправлять живую инфраструктуру.

## Статический режим

- Terraform/CloudFormation: exposure, wildcard IAM, encryption, security groups, logging и межресурсные privilege paths.
- Kubernetes manifests: RBAC, privileged/hostNetwork/hostPID/hostPath, Pod Security, NetworkPolicy, secrets, limits и storage.
- Docker/Compose/образы в платформенном контексте: user, capabilities, mounts, Docker socket, secrets в слоях, pins и supply chain.
- tfsec/checkov/trivy или эквивалент используй адресно, если доступны. Их вывод — кандидаты: проверь применимость и фактическую конфигурацию. Доступ к registry/account, установка сканеров и privileged kube-bench не становятся автоматически разрешёнными.

## Развёрнутая платформа

- AWS: STS identity, IAM/actions/resources/PassRole, отсутствие MFA и неиспользуемые admin access keys, публичность S3/RDS/ES, сети/metadata, CloudTrail/GuardDuty. Аналоги Azure/GCP проверяй в их модели identity и сетевых правил.
- K8s: текущий context, effective RBAC, service accounts, workloads, ingress/exposure, секреты по метаданным, admission и изоляция сети.
- Анализируй цепочку account/pod → node → cluster/admin по реальным правам и предусловиям. Не доказывай privesc живой модификацией без разрешения.
- Если доступен только IaC, явно ограничь вывод IaC: он не доказывает runtime-состояние. Дефекты кода приложения передай в security-review без повторного полного аудита.

## Отчёт

Группируй по account/cluster. Для находки: resource/file:line, identity/context, доказательство, exploit/privesc path, blast radius, важность, минимальная правка и что осталось непроверенным. Обезличивай чувствительные данные. Авторизованные исправления выполняй только по действующим рельсам и гейтам проекта.
