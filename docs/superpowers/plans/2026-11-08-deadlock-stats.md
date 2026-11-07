# Deadlock Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Веб-сервис на VPS: загрузка `.dem` Deadlock → разбор → интерактивный отчёт по матчу по ссылке; плюс визитка на GitHub Pages.

**Architecture:** Docker Compose: контейнер `app` (FastAPI + Python-конвейер + бинарник `dldump` на Rust) и `caddy` (HTTPS для `<ip>.sslip.io`). Демка → `dldump` → `msgs.tsv`/`ents.jsonl` во временной папке → `pipeline.process()` → `data/matches/<match_id>/`. Фронтенд — статичные страницы без сборщика, данные матча из `/api/matches/<id>`.

**Tech Stack:** Rust (haste @ d2cdbf2c + наш патч), Python 3.12, FastAPI, uvicorn, protobuf, pytest; фронт — vanilla JS + uPlot 1.6.31 (cdnjs); Caddy 2; Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-08-deadlock-stats-design.md`

## Global Constraints
- Весь интерфейс на русском.
- Загрузка: максимум 600 МБ, первые 8 байт файла = `PBDEMS2\0`, иначе отказ с понятной ошибкой.
- Исходная демка удаляется после обработки, в т.ч. при ошибке.
- Хранилище: `DATA_DIR/matches/<match_id>/` (по умолчанию `DATA_DIR=/data`); удаление папки = удаление матча, сервис не должен ломаться.
- Одна обработка одновременно; остальные в очереди.
- Каждая страница: `<meta name="robots" content="noindex">`; `/robots.txt` = `Disallow: /`.
- Без регистрации и паролей.
- Прототип в `prototype/` — источник истины по логике разбора; фикстуры эталонного матча 133162474 в `tests/fixtures/`.

## Review Focus
1. Демка нового патча, на которой `dldump` падает → пользователь видит «Не удалось разобрать демку (возможно, вышел патч игры)», временный файл удалён, сервис жив. (Task 3 test `test_parser_failure_cleans_up`)
2. Демка без `PostMatchDetails` (матч не доигран / записан не до конца) → понятная ошибка «В демке нет итогов матча», не 500. (Task 2 test `test_missing_postmatch`)
3. Две одинаковые демки загружены одновременно → обработка одна, оба получают тот же `match_id`. (Task 3 test `test_concurrent_duplicate`)
4. Обрыв загрузки посередине → частичный файл удалён. (Task 3 test `test_aborted_upload_cleanup`)
5. Ник игрока с HTML/`</script>`/невидимыми символами → отображается текстом, без XSS. (Task 4 test `escapes player names`)

---

### Task 1: Linux-сборка парсера `dldump` (Codex: gpt-6.1-sol, high)

**Files:**
- Create: `parser/Dockerfile` (multi-stage: rust builder → артефакт `/out/dldump`)
- Create: `parser/build.sh` (клонирует haste @ `prototype/haste-patch/HASTE_COMMIT`, применяет `haste.patch`, кладёт `dldump.rs` в `examples/`, `cargo build --release --example dldump --features "deadlock preserve-metadata"`)
- Create: `parser/protos.sh` — генерирует Python-модули из valveprotos-rs @ `prototype/VALVEPROTOS_REV` (`protos/common`, `gcsdk`, `deadlock`) в `app/proto/`
- Test: `parser/test_dldump.sh`

**Interfaces:**
- Produces: CLI `dldump <demo.dem> <outdir>` → `outdir/ents.jsonl`, `outdir/msgs.tsv`, `outdir/classes.tsv`, exit≠0 при ошибке. Формат идентичен прототипу.
- Produces: пакет `app/proto/` с `citadel_usermessages_pb2`, `citadel_gcmessages_common_pb2`.

- [ ] Step 1: `parser/test_dldump.sh <demo>`: запускает бинарник, проверяет exit 0, что в `msgs.tsv` есть строка с id `316`, и что `ents.jsonl` содержит `"tag":"final"` с `m_iszPlayerName`.
- [ ] Step 2: собрать образ, прогнать тест на `133162474.dem` (лежит у владельца; путь передаётся аргументом) — PASS. Число строк `msgs.tsv` = 142588 ± 0.
- [ ] Step 3: в patch проверить, что `eprintln!("WARN ...")`/`VARTYPE FAIL` остаются только в stderr.
- [ ] Step 4: Commit `feat(parser): linux build of dldump`.

### Task 2: Конвейер разбора `app/pipeline` (Codex: gpt-6.1-sol, medium)

**Files:**
- Create: `app/pipeline/refs.json` (из `prototype/pipeline/refs.json`), `app/pipeline/process.py`, `app/pipeline/errors.py`
- Create: `tools/build_refs.py` (перенос `prototype/pipeline/refs.py`; запускается на Windows-ПК владельца с игрой)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: формат `msgs.tsv`/`ents.jsonl` из Task 1, `app/proto/`.
- Produces: `process(raw_dir: Path, header: dict, out_dir: Path) -> dict` — пишет `out_dir/match.json`, `postmatch_raw.json`, `players.csv`, `items.csv`, `deaths.csv`, `chat.csv`, `gold.csv`; возвращает `summary = {matchId, startUTC, duration, winner, heroes: [{hero, team, name}], score: {amber, sapphire}}`.
- Produces: `class DemoError(Exception)` с полем `user_message: str` (рус.).
- `match.json` = формат прототипа **плюс**:
  - `playerTimeline`: `{ "<slot>": [{t, netWorth, kills, deaths, assists, level, lastHits, heroDamage}] }` — из снимков `ents.jsonl` (каждые 30 с) по `m_unLobbyPlayerSlot`;
  - `goldTimeline`: `{ "<slot>": [{t, <источник>: золото}] }` из `meta.players[].stats[]` (`goldSources` по `timeStampS`);
  - `gpm` у каждого игрока = `round(netWorth / (durationS/60))`;
  - `players[].gold` и `goldDeathLoss` как в прототипе; `laneColor` нет.

- [ ] Step 1: тесты на фикстурах (`tests/fixtures/133162474.*`, распаковка gz во tmp):
  - `test_reference_match`: `winner == "Sapphire (Archmother)"`, `duration == "32:51"`, игрок `Игрок 8`: hero `Виктор`, K/D/A 26/1/18, netWorth 60851, gpm 1852; `len(killFeed) == 93`; `len(chat) == 33`.
  - `test_player_timeline`: 12 слотов, у каждого ≥ 60 точек, последняя `netWorth` Игрок 8 в пределах 60851 ± 1000, `t` монотонно растёт.
  - `test_matches_prototype`: `players`, `objectives`, `damageMatrix` совпадают с `133162474.expected.json`.
  - `test_missing_postmatch`: `msgs.tsv` без строк 316 → `DemoError`, `user_message == "В демке нет итогов матча (матч не доигран или запись оборвана)"`.
- [ ] Step 2: запустить — FAIL.
- [ ] Step 3: реализовать `process` переносом `prototype/pipeline/analyze.py` (без print, пути через параметры, без чтения `%TEMP%`).
- [ ] Step 4: `pytest tests/test_pipeline.py -v` — PASS.
- [ ] Step 5: Commit `feat(pipeline): demo data processing`.

### Task 3: Сервер `app/server` (Codex: gpt-6.1-terra, medium)

**Files:**
- Create: `app/server/main.py`, `app/server/jobs.py`, `app/server/storage.py`, `app/requirements.txt`, `Dockerfile` (корень: копирует `/out/dldump` из образа Task 1)
- Test: `tests/test_server.py` (dldump подменяется фейком через env `DLDUMP_BIN`)

**Interfaces:**
- Consumes: `process(...)`, `DemoError` (Task 2); CLI `dldump` (Task 1).
- Produces HTTP API:
  - `POST /api/upload` (multipart `file`) → `202 {"job": "<id>"}` или `200 {"matchId": "...", "duplicate": true}`; `413` > 600 МБ; `400 {"error": "Это не демка Deadlock"}` при неверной сигнатуре.
  - `GET /api/jobs/<id>` → `{"state": "queued"|"processing"|"done"|"error", "position": int, "matchId"?: str, "error"?: str}`.
  - `GET /api/matches` → список `summary` (новые сверху; папки без `match.json` пропускаются).
  - `GET /api/matches/<id>` → `match.json`; `GET /api/matches/<id>/files/<name>` → CSV/JSON.
  - `GET /`, `/m/<id>` → статика `web/`; `/robots.txt`.
- Дедупликация: sha256 потока при записи; индекс `DATA_DIR/hashes.json` sha256→matchId; после разбора — если папка `matches/<matchId>` уже есть, вернуть её.
- Очередь: один воркер (`asyncio` + `run_in_executor`), `dldump` с таймаутом 300 с.

- [ ] Step 1: тесты: `test_upload_rejects_non_demo`, `test_upload_too_large` (лимит через env `MAX_UPLOAD_MB=1`), `test_full_flow` (фейковый dldump копирует фикстуры → job done → `/api/matches` содержит 133162474), `test_duplicate_same_hash`, `test_concurrent_duplicate`, `test_parser_failure_cleans_up` (фейк exit 1 → state error, `user_message` = «Не удалось разобрать демку (возможно, вышел патч игры)», tmp-файлов нет), `test_aborted_upload_cleanup`, `test_deleted_match_folder` (удалили папку → список без него, 404 на матч), `test_robots`.
- [ ] Step 2: FAIL → реализовать → PASS.
- [ ] Step 3: Commit `feat(server): upload, queue, storage api`.

### Task 4: Фронтенд `web/` (Codex: gpt-6-astra, medium)

**Files:**
- Create: `web/index.html` (загрузка drag&drop с прогрессом + список матчей), `web/match.html`, `web/js/match.js`, `web/js/charts.js`, `web/css/style.css`
- Test: `tests/web/match.spec.ts` (Playwright, сервер с фикстурой из Task 3)

**Interfaces:**
- Consumes: API из Task 3, формат `match.json` из Task 2 (`playerTimeline`, `goldTimeline`, `gpm`, остальное как в `prototype/pipeline/report_template.html`).

Требования (визуальная база — `prototype/pipeline/report_template.html`, светлая/тёмная тема):
- График «Нетворс»: линия на каждого игрока (цвет героя; оттенки янтарные/синие по команде), легенда-чекбоксы по игроку + кнопки «Amber», «Sapphire», «Все», «Суммы команд»; при наведении — вертикальная линия и подсказка со значениями всех видимых игроков, отсортированными по убыванию. Переключатель метрики: нетворс / GPM по времени / убийства / урон / уровень.
- «Получено золота»: таблица как в прототипе + график накопленного золота по источникам для выбранных игроков.
- Клик по игроку (в любой таблице/легенде) → панель-карточка: билд по времени (проданные зачёркнуты), способности, смерти списком + на мини-карте, урон кому/от кого (строка/столбец матрицы).
- Остальные разделы прототипа сохранить.
- Мобильная ширина 390 px без горизонтальной прокрутки страницы (таблицы скроллятся внутри).
- Все строки из данных вставляются через `textContent`/экранирование.

- [ ] Step 1: Playwright-тесты: `loads match`, `toggle player line` (снять чекбокс Игрок 8 → серия скрыта), `tooltip shows 12 values`, `player card opens`, `escapes player names` (фикстура с ником `<img src=x onerror=alert(1)>` → нет `img` в DOM), `upload shows progress then redirects`, `mobile no horizontal scroll`.
- [ ] Step 2: FAIL → реализовать → PASS.
- [ ] Step 3: Commit `feat(web): interactive match report`.

### Task 5: Деплой (Codex: gpt-6-luna, low)

**Files:**
- Create: `deploy/docker-compose.yml` (сервисы `app`, `caddy`; том `./data:/data`), `deploy/Caddyfile` (`{$SITE_HOST}` → `app:8000`, `request_body max_size 650MB`), `deploy/install.sh`

`install.sh` на чистом Ubuntu: ставит Docker, определяет публичный IP (`curl -s https://api.ipify.org`), `SITE_HOST=<a-b-c-d>.sslip.io`, `docker compose up -d --build`, печатает итоговую ссылку.

- [ ] Step 1: `docker compose config` валиден; `shellcheck deploy/install.sh` без ошибок.
- [ ] Step 2: локально `SITE_HOST=localhost docker compose up` → `curl -k https://localhost/robots.txt` = `Disallow: /`.
- [ ] Step 3: Commit `feat(deploy): compose + caddy + installer`.

### Task 6: README + визитка GitHub Pages (Codex: gpt-6-luna, low)

**Files:**
- Create: `README.md` (рус.: что это, скриншоты, установка одной командой, обновление справочников после патча через `tools/build_refs.py`, удаление матча = удалить папку)
- Create: `site/index.html` (визитка, без ссылки на инстанс владельца), `.github/workflows/pages.yml` (деплой `site/` в Pages)
- [ ] Step 1: открыть `site/index.html` локально, проверить ссылки. Commit `docs: readme and pages site`.

### Task 7: Выкладка (владелец + я)
- [ ] Создать публичный репозиторий на GitHub, push, включить Pages (Actions).
- [ ] На VPS: `git clone … && sudo bash deploy/install.sh`; загрузить `133162474.dem`, сверить отчёт с эталоном.
- [ ] Финальное ревью всей ветки — Codex gpt-6.1-sol high.
