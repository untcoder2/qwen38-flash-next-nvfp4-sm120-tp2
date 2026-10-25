# LLM-сервер — рабочий каталог

Локальная LLM-инфраструктура: GPU-хост с SGLang, который потребляет opencode на
десктопе. Этот каталог — записи и заметки; сам сервер живёт на удалённой машине.

**Читать первым:** [docs/architecture.md](docs/architecture.md) — что где стоит,
как запускается, какими параметрами. [docs/notes.md](docs/notes.md) — журнал работ:
что меняли, почему и чем обернулось.

## Быстрый доступ

```bash
# подключение (ключ уже на месте)
ssh -i ~/.ssh/molochko_deploy molochko@192.168.2.127

# тулзы на хосте, не на Windows (обёртки; см. AGENTS.md)
ops/rsh.sh 'nmap -sV <target>'          # команда на хосте, exit-код пробрасывается
ops/rput.sh ops/_probe.py               # файл -> ~/agent-tools/ на хосте
ops/rget.sh /home/molochko/agent-tools/out.json   # отчёт оттуда

# состояние
systemctl is-active sglang && curl -sk --ssl-no-revoke https://192.168.2.127:1234/v1/models -H "authorization: Bearer sk-local"

# рестарт (пароль не нужен, есть NOPASSWD на этот юнит)
sudo systemctl restart sglang
```

## Главное в одной строке

Модель `Qwen3.8-Flash-Next` (лист `-qadmtp`, с 19.10) на TLS-порту 1234, имя для API
`qwen3.8-flash-next`, контекст 786 432; скорость — по current-config (fixed-w4 ~357.6,
агентная база suite ~199). TLS с 14.10: CA `certs/qwen-home-ca.pem`.
