# Steam Login & Player Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Steam OpenID login, public player profiles with recent matches from deadlock-api, and login-gated "process this match" with 2/hour per account.

**Architecture:** New `app/server/auth.py` (OpenID + signed cookie session, stdlib hmac), `app/server/players.py` (deadlock-api proxy with 5-min cache), per-account limit persisted in sqlite. Frontend: shared `web/js/auth.js` header widget, new `web/profile.html` + `web/js/profile.js`, match page fallback for unprocessed numeric matches.

**Tech Stack:** FastAPI, httpx, sqlite3, vanilla JS (style v2), pytest + Playwright.

**Spec:** `docs/superpowers/specs/2026-11-08-steam-login-profiles-design.md`

## Global Constraints
- UI text Russian; all files UTF-8 (never `?` in place of Cyrillic).
- No new Python dependencies (stdlib hmac/hashlib/secrets for cookie signing).
- Session cookie `dls_session`: HttpOnly, Secure (unless `COOKIE_INSECURE=1` for tests), SameSite=Lax, Max-Age 30 days.
- Secret: env `SESSION_SECRET`, else generated once into `$DATA_DIR/session_secret` (mode 600). Never in repo.
- Site origin: env `PUBLIC_URL` (e.g. `https://45-13-234-10.sslip.io`), used for OpenID `return_to`/`realm`.
- account_id = SteamID64 − 76561197960265728.
- Per-account processing limit: env `PER_ACCOUNT_FETCH_PER_HOUR`, default **2**. Existing limits (MAX_QUEUE 5, FETCH_PER_HOUR 60, disk) unchanged.
- deadlock-api cache TTL 300 s; upstream timeout 15 s; API failure → 503 `{"error":"API временно недоступен"}`, never 500.
- `/api/upload` stays anonymous.

## Review Focus
1. Forged OpenID callback (wrong `openid.op_endpoint`, `claimed_id` not `https://steamcommunity.com/openid/id/<17 digits>`, `return_to` mismatch, Steam `check_authentication` says `is_valid:false`) → no session.
2. Tampered/expired cookie → treated as logged out (no 500).
3. Parallel fetch requests from one account exceed 2/hour → third gets 429 (limit taken synchronously before any await).
4. Already processed match → `duplicate`, does NOT consume account limit.
5. deadlock-api down / returns garbage → profile shows error, report pages unaffected.

---

### Task 1: Auth (OpenID + session)

**Files:** Create `app/server/auth.py`; Modify `app/server/main.py` (mount router in `create_app`); Test `tests/test_auth.py`.

**Interfaces — Produces:**
- `current_account(request) -> int | None` (account_id from valid cookie)
- Routes: `GET /auth/login` (302 to Steam), `GET /auth/callback` (verify, set cookie, 302 to `/p/<account_id>`), `POST /auth/logout`, `GET /api/me` → `{"accountId": int, "steamId": str} | {"accountId": null}`.
- Cookie value: `base64(json{"a":account_id,"exp":unix}) + "." + hmac_sha256_hex`.
- Steam verification via injectable `app.state.steam_verify: async (params: dict) -> bool` (default posts `openid.mode=check_authentication` to `https://steamcommunity.com/openid/login`, valid iff body contains `is_valid:true`).

- [ ] Step 1: tests: `test_login_redirects_to_steam` (Location starts with `https://steamcommunity.com/openid/login`, contains `openid.return_to=<PUBLIC_URL>/auth/callback`); `test_callback_valid_sets_cookie` (stub verify→True, claimed_id `https://steamcommunity.com/openid/id/76561198000000001` → cookie set, `/api/me` returns accountId 39734273); `test_callback_rejected_when_steam_says_invalid`; `test_callback_rejects_bad_claimed_id`; `test_callback_rejects_wrong_return_to`; `test_tampered_cookie_is_logged_out`; `test_expired_cookie_is_logged_out`; `test_logout_clears`.
- [ ] Step 2: run `python -m pytest tests/test_auth.py -q` → FAIL.
- [ ] Step 3: implement.
- [ ] Step 4: run → PASS; full `python -m pytest -q` passes.
- [ ] Step 5: commit `feat(auth): Steam OpenID login`.

### Task 2: Login-gated fetch with per-account limit

**Files:** Modify `app/server/main.py` (`/api/fetch`), `app/server/storage.py` (limit table + requester); Test `tests/test_account_limits.py`; update existing fetch tests to log in (helper sets cookie).

**Interfaces — Consumes:** `current_account`. **Produces:** `Storage.take_account(account_id: int, limit: int) -> int` (0 = ok, else retry seconds; sqlite table `account_jobs(account_id, ts)`, rolling 3600 s), `meta.json` of fetched match gets `"requestedBy": account_id`.

Order in `/api/fetch`: not logged in → 401 `{"error":"Войдите через Steam, чтобы обработать матч"}`; parse id; already processed → duplicate (no limit consumed); job already active → return it (no limit consumed); `admit()`; `take_account` → 429 `Лимит: 2 обработки в час, попробуйте через N мин`; upstream limit; submit.

- [ ] Step 1: tests: `test_fetch_requires_login` (401); `test_account_limit_two_per_hour` (3rd → 429); `test_duplicate_does_not_consume_limit`; `test_limit_survives_restart` (new app, same DATA_DIR → still 429); `test_requested_by_recorded`.
- [ ] Step 2–4: fail → implement → pass (full suite).
- [ ] Step 5: commit `feat: login-gated fetch, 2 per hour per account`.

### Task 3: Players API + routes

**Files:** Create `app/server/players.py`; Modify `main.py`; Test `tests/test_players.py`.

**Interfaces — Produces:**
- `GET /api/players/{id}` (id = account_id or SteamID64) → `{"accountId", "name", "avatar", "matches":[{"matchId","heroId","startTime","kills","deaths","assists","netWorth","durationS","won","state":"processed"|"queued"|"available"}]}` (last 30 from `/v1/players/{account_id}/match-history`; name/avatar from `https://steamcommunity.com/profiles/<steamid64>?xml=1` `steamID`/`avatarMedium`, fallback name = steamid; `won` = `match_result == player_team`; state from storage/jobs).
- `GET /api/remote/{match_id}` → trimmed `/v1/matches/{id}/metadata` `match_info`: duration, winningTeam, players[{accountId, heroId, team, kills, deaths, assists, netWorth}].
- `GET /p/{id}` → `web/profile.html`.
- `GET /api/search?q=` → `{"redirect": "/m/<id>" | "/p/<account_id>"}`: numeric ≤ 11 digits → match; 17-digit or `steamcommunity.com/profiles/<17>` → profile; `steamcommunity.com/id/<vanity>` → 400 `Используйте ссылку вида /profiles/<число>` (no API key for vanity).
- Upstream calls via injectable `app.state.http_get: async (url) -> (status, bytes)`; cache dict `url → (ts, value)` TTL 300.

- [ ] Step 1: tests with stubbed http_get: profile shape + states; cache (two calls → one upstream); upstream error → 503; invalid id → 400; search cases above.
- [ ] Step 2–4: fail → implement → pass.
- [ ] Step 5: commit `feat: player profiles API`.

### Task 4: Frontend

**Files:** Create `web/js/auth.js`, `web/profile.html`, `web/js/profile.js`; Modify `web/index.html` (search box, header), `web/match.html` + `web/js/match.js` (header widget; nicknames link to `/p/<accountId>`; when `/api/matches/<id>` is 404 and id numeric → render `/api/remote/<id>` summary + «Обработать» button), `web/css/style.css`; Test `tests/web/profile.spec.ts`.

Header widget (all pages, top-right): logged out → «Войти через Steam» (link `/auth/login`); logged in → avatar + name → `/p/<me>`, «Выйти».
Process button: logged out → disabled text «Войдите через Steam, чтобы обработать»; click → `POST /api/fetch`, poll `/api/jobs/<id>`, on done swap to «Обработан» link; show server `error` text on 4xx/5xx.
Mobile 390px, dark v2 style, team colors, portraits `web/img/heroes/<heroId>.png`.

- [ ] Step 1: Playwright (routes mocked): header logged-out/in; profile lists matches with three states; process button disabled when logged out, flow to «Обработан» when logged in; 429 message shown; unprocessed match page renders remote summary; nickname links to profile; search redirects; 390px no horizontal scroll.
- [ ] Step 2–4: fail → implement → pass (all Playwright tests).
- [ ] Step 5: commit `feat(web): Steam login, profiles, process button`.

### Task 5: Deploy config

**Files:** Modify `deploy/docker-compose.yml` (env `PUBLIC_URL=https://${SITE_HOST}`, `PER_ACCOUNT_FETCH_PER_HOUR=2`), `README.md` (one paragraph: login, limits).
- [ ] Verify `docker compose config` OK; full pytest + Playwright pass; commit `chore(deploy): login env`.
