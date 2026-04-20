---
description: "Use when: troubleshooting errors, debugging failures, fixing broken builds, resolving test failures. Contains known pitfalls Copilot has hit before in this repo."
applyTo: "**"
---
# Known Pitfalls

Patterns Copilot has gotten stuck on before. Each entry = a loop that was caught, diagnosed, and resolved. Check here before spiraling.

<!-- 
Format:
## Short description
- **Symptom**: What it looked like
- **Wrong fix**: What was tried and failed
- **Actual fix**: What worked
- **Why**: Root cause in one line
-->

## Alpaca News API client is separate from StockHistoricalDataClient
- **Symptom**: `'StockHistoricalDataClient' object has no attribute 'get_news'`
- **Actual fix**: Import `from alpaca.data.historical.news import NewsClient` and pass `symbols=",".join(tickers)` (string, not list). Response shape: `news_set.data["news"]` returns `List[News]`, not `news_set.news`.

## Claude 4.x models reject assistant prefill for JSON mode
- **Symptom**: 400 `invalid_request_error: This model does not support assistant message prefill. The conversation must end with a user message.`
- **Wrong fix**: Appending `{"role": "assistant", "content": "{"}` to messages (worked on Claude 3.x).
- **Actual fix**: Drop the prefill, strengthen the user prompt ("Respond with ONLY the JSON object"), and use a brace-matching extractor on the response to pull the first `{...}` block.

## Claude model IDs drift — don't hardcode dated versions
- **Symptom**: 404 on `claude-sonnet-4-20250514` despite it being valid at dev time.
- **Actual fix**: Make model IDs env-configurable (`CLAUDE_SONNET_MODEL`, `CLAUDE_HAIKU_MODEL`). List available models via `GET https://api.anthropic.com/v1/models` when debugging.

## Windows console encoding chokes on unicode in headlines
- **Symptom**: `'charmap' codec can't encode character '\u2011'` printing news headlines.
- **Actual fix**: At top of `heron/cli.py`, `sys.stdout.reconfigure(encoding="utf-8")` on Windows.

## Flask test client + shared sqlite connection = closed-db errors
- **Symptom**: `sqlite3.ProgrammingError: Cannot operate on a closed database` when dashboard routes POST and re-query.
- **Wrong fix**: Returning the same shared connection from the test fixture's `get_journal_conn` mock.
- **Actual fix**: Fixture yields a `db_path`, and `get_journal_conn` `side_effect` returns a fresh connection per call (matches production where each request gets its own connection).

## Order client_order_id must be deterministic per trade for retries
- **Symptom**: Generating `make_client_order_id(strategy, ticker, side)` inside executor methods produced a fresh ms-nonce on every call. A 30s exit-poll resubmitting after a journal-write failure would create a duplicate sell at the broker. Same shape on entries with caller-side retries.
- **Wrong fix**: Relying on Alpaca's 422 "client_order_id must be unique" string match — only catches when the SDK raises that exact APIError; ConnectionError/Timeout/5xx slip through.
- **Actual fix**: Use deterministic helpers `make_entry_order_id(strategy, candidate_id, ticker, side)` and `make_close_order_id(strategy, trade_id, ticker, side)`. On any non-APIError submit failure, call `broker.get_order(client_order_id)` before deciding whether to retry.

## Alpaca client_order_id charset is restricted
- **Symptom**: Considered using `|` as a separator in client_order_ids since strategy ids contain underscores.
- **Actual fix**: Alpaca only allows `[A-Za-z0-9._-]`. Stick with `_` and treat the resulting ID as opaque (don't `split("_")` to parse it — strategy ids contain `_`).

## Risk checks must be mode-aware
- **Symptom**: `pre_trade_checks` queried trades/wash-sale/PDT mode-agnostically. Paper trades blocked live entries via PDT counts and wash-sale lots; paper open positions ate the live exposure budget.
- **Actual fix**: Thread `mode="paper"|"live"` through `pre_trade_checks` and each `check_*` helper. Wash-sale and PDT short-circuit to "pass" in paper mode (they're tax/broker rules on real money). Exposure / position count / daily entries / daily loss filter `list_trades(mode=mode)` so each mode sees only its own state.

## get_bars cache returned partial ranges silently
- **Symptom**: First call cached Mon–Tue; later call asking Mon–Fri returned only Mon–Tue. Strategy traded on stale data.
- **Actual fix**: In `data/alpaca_market.fetch_bars`, compare cached coverage to requested end; if cache ends before the requested end-date, fetch the delta starting the day after the latest cached bar.

## Demo's baseline strategy id must match `ensure_baseline` convention
- **Symptom**: `cli.py journal demo` created `pead_v1_base`; `strategy/baseline.ensure_baseline` constructs `f"{parent}_baseline"`. Demo's baseline was invisible to the pairing/beat-test machinery.
- **Actual fix**: Use `pead_v1_baseline` everywhere. If you ever change the suffix, change `ensure_baseline` and grep for the literal `_base` / `_baseline` callsites.

## Dashboard port: 5001 is canonical
- All of `cli.py dashboard --port`, `config.DASHBOARD_URL`, README, `.env.example` must agree. Mismatches break Discord deep-links built from `DASHBOARD_URL`.

## FK constraints in journal trades require real candidate rows in tests
- **Symptom**: Passing `candidate_id=42` to `enter_position` from a test without first creating the candidate row → `sqlite3.IntegrityError: FOREIGN KEY constraint failed` from `create_trade`.
- **Actual fix**: In tests, `create_candidate(conn, strategy_id, ticker, ...)` first and pass the returned id.
