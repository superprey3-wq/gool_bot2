# GOOL — оригинальный футбол без обученных моделей

**Ветка:** `gool-football-original-no-ml` (основана на `main`, не на GOOL Lite).

**Стартовый файл:** `football_original_no_ml.py` на Python 3.11.\nТакже оставлен `bot_hosting_lite.py` — прежнее имя Startup в Bot-Hosting.net;\nв этой ветке оно запускает **оригинальный GOOL**, а не Lite.
Проверка импорта без сети: `python football_original_no_ml.py --smoke`.

Это отдельный однопроцессный запуск сохранённых модулей GOOL:

- **LIVE:** исходный `StorageLiveSnapshotCollector` → `LiveMomentumTracker` →
  оригинальная `build_brain_v3_memory` → `evaluate_brain_v3` и
  `evaluate_live_goals`.
- **PREMATCH:** оригинальные `forecast_from_history` и
  `production_v5_picks` с парными реальными котировками Flashscore.
- **Котировки LIVE:** оригинальный `RobustXBetMarketCollector` и
  `extract_xbet_score`, дополнительная проверка совпадения команд и счёта.
- **Отчёты и Telegram:** лёгкая новая оболочка; исходные математические мозги
  не переписаны.
- **Отключено:** хоккей, баскетбол, обучение и загрузка ML-моделей,
  Chromium, Matchbook, мультиспорт и SUPER 10.

## Настройка Bot-Hosting.net

Используйте **именно эту ветку** в настройке GitHub и файл
`football_original_no_ml.py` в Startup. Если GitHub-подключение жёстко
закреплено за `bot-hosting-lite`, ручной рестарт **не переключит** ветку;
сначала измените ветку в панели.

Не запускайте GOOL Lite и оригинальный бот с одним Telegram-токеном одновременно:
оба могут читать `getUpdates`.

Обязательные секреты: уже существующие `TELEGRAM_BOT_TOKEN` и
`TELEGRAM_CHAT_ID`. Не публикуйте значения токенов.

`GOOL_ORIGINAL_FOOTBALL_MODE=shadow` — стандартное значение, **только
анализ, Telegram не рассылает ставки**. Команды владельца бота:
`/start`, `/status`, `/audit`, `/prematch`, `/live`,
`/candidates`, `/journal`, `/dayreport`.
`/dayreport` пока текстовый краткий журнал отправленных сигналов,
не полноценный прежний HTML-отчёт.

Если источники, валидность ставок, память и стабильность проверены,
`GOOL_ORIGINAL_FOOTBALL_MODE=active` включает отправку,
но **не делает прогнозы гарантированными**.

Рекомендуемые параметры на бесплатном хостинге: 
`LIVE_DETAIL_WORKERS=2`, `LIVE_HISTORY_WORKERS=1`,
`GOOL_ORIGINAL_LIVE_INTERVAL=90`, `GOOL_ORIGINAL_PRE_MAX=6`.
Уменьшайте покрытие PRE/интервал, только если фактические логи покажут
превышение лимитов.

**Ограничение:** memory smoke измеряет лишь стартовую загрузку;
полноценный непрерывный сбор всех активных матчей на лимите 256 МБ
пока не доказан. Из-за сети, ограничения CPU и недоступности 1xBet на
IP хостинга возможны пустые линии и медленные циклы. Реальную
доходность/проходимость никто не подтвердил.

Логи: `GOOL_ORIGINAL_FOOTBALL_NO_ML_STARTED` и
`GOOL_ORIGINAL_SCAN`. `/status` выводит охват Flashscore,
количество решений Brain V3, отказы 1xBet, память и длительность скана.
