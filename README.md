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

# состояние
systemctl is-active sglang && curl -s http://192.168.2.127:1234/v1/models

# рестарт (пароль не нужен, есть NOPASSWD на этот юнит)
sudo systemctl restart sglang
```

## Главное в одной строке

Модель `Qwen3.8-Flash-Next-NVFP4-bf16mtp` на порту 1234, имя для API
`qwen3.8-flash-next`, контекст 786 432, скорость ~243 tok/s одним потоком.
