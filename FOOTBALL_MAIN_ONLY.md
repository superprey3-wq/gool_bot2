# Оригинальный футбольный GOOL из `main` (не Lite)

В `main` появился отдельный **`football_main_start.py`**. Старые файлы
`monkey_start.py` и исходные модули не заменялись. Это точка запуска
оригинальных служб:

- `gool_bot2.storage_live_collector`: тот же постоянный сбор Flashscore,
  дополнительные 365Scores/FotMob и история давления
- `gool_bot2.xbet_market_worker`: тот же сбор котировок с защитой счёта
- `gool_bot2.storage_market_signal_worker_var`: основной мозг GOOL, карточки,
  журнал и Telegram
- `gool_bot2.v4_prematch_daemon`: оригинальный прематч, **только** если папка
  проекта именно `/home/container`; старый демон жёстко ссылается на этот путь

Хоккей, баскетбол, мультиспортивные службы, SUPER10, Matchbook, Chromium и
отдельные архиваторы отключены. Ограничены параллельные HTTP-запросы и история
котировок, **но формулы основного GOOL не переписаны**.

## Безопасная проверка перед переключением Bot-Hosting.net

В панели должен быть выбран **GitHub branch: `main`**, файл запуска
`football_main_start.py` (не `bot_hosting_lite.py`). Переключение ветки —
отдельная настройка на хостинге; добавление файла в GitHub не переключает
работающий сервер автоматически.

Запустить безопасную проверку:

```bash
python football_main_start.py --plan
python football_main_start.py --check
```

`--plan` показывает реальные оригинальные модули. `--check` проверяет,
доступны ли зависимости, Telegram, все три модели и нужная структура папок.

**Модели восстановлены из оригинального релиза GOOL.** Три файла не лежат
непосредственно в исходниках, но опубликованы в GitHub Releases
`raven-models-latest`. Ничего переобучать не нужно: безопасная команда

```bash
python football_main_start.py --fetch-models
```

вызывает существующую `raven_start.ensure_models()`, которая скачивает
`gool-raven-models.zip` и сверяет SHA-256 по манифесту. Полученные файлы
`archive_foundation.pkl`, `archive_hazard.pkl` и
`football_data_goal_models.pkl` сохраняются в
`gool_bot2_deploy/models/`. Для другого пути можно задать
`GOOL_MODEL_DIRECTORY`. Команда `--check` по-прежнему не загружает
никаких файлов.

Нужны библиотеки numpy, pandas, scikit-learn, pydantic, pyyaml, Pillow.
Launcher не скачивает их автоматически. **256 МБ RAM / 25% CPU могут быть
недостаточны для одновременного запуска оригинальных служб.** Зелёный
GitHub-тест без моделей не доказывает работоспособность на бесплатном
хостинге.

После успешной проверки ресурсов и модели запуск полностью оригинальной
цепочки (она может **отправлять реальные сигналы**):

```bash
GOOL_MAIN_FOOTBALL_ALLOW_SEND=1 python football_main_start.py --run
```

Этот флаг намеренно обязателен: в оригинальном worker нет режима
`SHADOW` из GOOL Lite. Не запускайте одновременно Lite и MAIN под одним
Telegram-токеном — они конкурируют за `getUpdates`.

В консоли ожидается `GOOL_ORIGINAL_MAIN_FOOTBALL_START`, затем
`GOOL_MAIN_FOOTBALL_CHILD`. При падении любого основного воркера
супервизор завершает запуск, а не запускает бесконечный цикл рестартов.
