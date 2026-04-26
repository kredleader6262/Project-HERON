# Project HERON

*Hypothesis-driven Execution with Research, Observation, and Notation*

An LLM-augmented algorithmic trading system on a $500 seed account. The LLM researches; deterministic code executes. The journal is the product.

## What This Is

A learning-first system that uses local and API language models to surface trading candidates, then validates and executes them through strict rule-based code with hard risk limits. Every decision is journaled. Every strategy runs against a deterministic baseline before touching real money.

**Full spec:** [`Project-HERON.md`](Project-HERON.md)

## Stack

Python 3.11+ · SQLite (WAL) · Flask + HTMX · Ollama (Qwen 2.5 7B) · Claude API · Alpaca (IEX tier) · YAML config · Tailscale · Discord webhooks

*Scheduler (APScheduler) is planned but not yet wired — research/exit loops are run on demand via the CLI for now.*

## Architecture

| Layer | Does |
|---|---|
| Data | Fetch/cache market data, news, filings |
| Research | LLM passes, strategy proposals, candidates |
| Strategy | Validate, size, wash-sale/PDT checks, decide |
| Execution | Submit orders, manage fills, reconcile |
| Journal & Dashboard | Persist every decision, serve web UI |

The LLM is never in the execution hot path. Layers don't cross boundaries.

## Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1   # Windows — or `source .venv/bin/activate` on Linux/Mac
pip install -e ".[dev]"
cp .env.example .env          # fill in API keys
```

For the full bootstrap (Ollama, Alpaca account, Discord, etc.) see [`Project-HERON.md` Section 18](Project-HERON.md).

## Quick Start

```bash
# Fetch today's bars and headlines for the watchlist (needs Alpaca API keys in .env)
heron data today

# Get a live quote with staleness check
heron data quote AAPL

# Journal demo — creates sample strategies, candidates, trades, wash-sale/PDT data
heron journal demo

# Journal status — summary of strategies, open trades, risk counters
heron journal status

# Launch the web dashboard (default port 5001)
heron dashboard                  # http://127.0.0.1:5001
heron dashboard --port 8080      # custom port

# Run tests
python -m pytest tests/ -v
```

## Local LLM (Ollama)

Research passes use a repo-local Ollama install in `tools/ollama/` (gitignored).

```bash
heron ollama status              # show install + runtime state
heron ollama start               # start server on 127.0.0.1:11434
heron ollama pull                # pull the default model (OLLAMA_MODEL env var)
heron ollama list                # list installed models
heron ollama stop                # stop the server
```

Default model: `qwen2.5:7b-instruct-q4_K_M` (~4.7GB). Override via `OLLAMA_MODEL` in `.env`.

## Research Workflow

```bash
# Run a research pass (premarket or midday) for a strategy
heron research run --strategy pead_v1 --pass-type premarket

# Check Ollama / model availability
heron research status

# Have Claude write a thesis for an existing candidate
heron research thesis <candidate_id>

# Have Claude propose a new strategy (operator-gated)
heron research propose --context "VIX above 25, defensive rotation"

# After review:
heron journal approve <strategy_id>     # PROPOSED → PAPER (creates baseline)
heron journal reject <strategy_id> -r "reason"
heron journal inbox                     # show PROPOSED strategies
```

## Configuration

- `config.yaml` — watchlist, news sources, timeframes, cost ceiling, alert/audit thresholds. Edit in place; *do not commit machine-specific changes*.
- `.env` — secrets and per-machine overrides (`ALPACA_*`, `ANTHROPIC_API_KEY`, `DISCORD_WEBHOOK_URL`, `DASHBOARD_URL`, model IDs). Copy from `.env.example`. Never commit.
- `data/heron.db` — the journal (SQLite, WAL). Auto-created on first run; gitignored.
- `logs/` — rotating logs. Gitignored.
- `tools/ollama/`, `tools/ollama-models/` — local Ollama binary + model cache. Gitignored (multi-GB).

## Run It Daily

```bash
heron ollama start          # one-time per session
heron run                   # foreground supervisor (Ctrl+C to stop)
```

`heron run` schedules everything: pre-market research, executor cycles every 5 min during market hours, EOD debrief, daily health check, hourly heartbeat. All runs are journaled to `scheduler_runs`. The dashboard at `/scheduler` shows live status and lets you queue **Run Now / Pause / Resume** commands (picked up within ~10 s).

Useful flags:

```bash
heron run --status                       # print job schedule + recent runs
heron run --once research_premarket      # fire one job synchronously
heron run --skip-preflight               # bypass preflight (testing only)
```

### Running unattended

The supervisor is just a process — wrap it however you like.

**Windows (NSSM):**
```powershell
nssm install HERON "C:\path\to\.venv\Scripts\python.exe" "-m" "heron" "run"
nssm set HERON AppDirectory "C:\source\Project-HERON"
nssm start HERON
```

**Linux (systemd):** drop in `/etc/systemd/system/heron.service`:
```ini
[Unit]
Description=HERON trading supervisor
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/heron
ExecStart=/opt/heron/.venv/bin/python -m heron run
Restart=on-failure
User=heron

[Install]
WantedBy=multi-user.target
```
Then `sudo systemctl enable --now heron`.

The dashboard runs as a separate process — see [`heron.dashboard`](heron/dashboard/__init__.py).

## Status

**Milestones 1–15 complete.** See [`ROADMAP.md`](ROADMAP.md) for full progress.

| Milestone | What | Tests |
|---|---|---|
| M1 Data Layer | OHLCV/news fetch, cache, sanitizer, RSS | 48 |
| M2 Journal Schema | 10-table SQLite schema, CRUD, state machine | 47 |
| M3 Strategy Framework | Base class, 8 risk checks, position sizing | 33 |
| M4 PEAD Strategy | Post-earnings drift screen/levels/exit | 20 |
| M5 Execution Layer | Broker adapter, executor, virtual stops, reconciliation | 11 |
| M6 Dashboard v1 | Flask + HTMX + Tailwind, 6 views | 8 |
| M7 Research (Local) | Ollama client, classifier, candidate generator, orchestrator | 21 |
| M8 Research (Claude) | Claude API client, thesis writer, escalation, audit sampling | 20 |
| M9 Baseline Runner | Deterministic twin, equity curves, bootstrap beat test | 24 |
| M10 Proposal Flow | Claude proposer, dashboard approval UI, operator workflow | 19 |
| M11 Audit System | Cost-triggered post-mortems, trust score, `/audits` view | 16 |
| M12 Alerts + Debrief | Discord webhook, rate-limited alerts, EOD Claude prose | 17 |
| M13 Backtester | Deterministic replay, cost model, memorization flag, `/backtests` | 15 |
| M14 Cost Controls | `cost_guard` (projection, warn/trip), Discord alerts, `/costs` | 14 |
| M15 Resilience | Startup audit, graceful shutdown, secrets hygiene, `/resilience` | 20 |

**331 tests passing.** Integration tests pending API keys.
