# Project HERON

*Hypothesis-driven Execution with Research, Observation, and Notation*

A learning-first algorithmic trading system with an LLM research layer, designed to run indefinitely on a small seed account under strict operator control.

**Seed:** $500
**Horizon:** Indefinite
**Posture:** Learning-First, Operator-Gated

*Final Scoped Plan*

---

## 1. Principles

**About the name.** A heron stands still at the edge of the water, watches for a long time, strikes only when conditions are right, and returns to watching. That is what this system does. HERON is an acronym and a reminder: *hypothesis-driven execution with research, observation, and notation.* The research is the LLM layer. The observation is the journal. The notation is the rationale attached to every decision. The strike is deterministic. Most of the time, the bird is not moving.

### 1.1 Primary Purpose

**HERON is a learning system and engineering portfolio piece that happens to trade real money.** The empirical evidence on LLM-driven retail stock prediction is clear: published alpha decays rapidly as LLMs diffuse (Sharpe 6.54 in 2021Q4 collapsed to 1.22 by mid-2024 in Lopez-Lira and Tang's own data), measured returns are cost-sensitive (350% at 10 bps transaction costs collapses to ~50% at 25 bps), and most public backtests are contaminated by LLM memorization of pre-cutoff data. A $500 account on Alpaca's IEX feed will eat 25 bps or more in round-trip costs. This document assumes all of that.

HERON is not a credible path to beat the S&P 500 at this size. It is a credible learning system, a useful engineering portfolio piece, and a way to produce a disciplined LLM-annotated trading journal that is genuinely rare among retail traders. The guiding principle is that boring execution with edge beats interesting execution without it. The LLM does research, hypothesis generation, and post-hoc explanation. Deterministic code handles entries, exits, sizing, and risk. The operator decides when a strategy graduates.

### 1.2 The Three Commitments

**Learning-first.** No external capital is added to the account. The $500 is risk capital, fully disposable. The account grows only through reinvested profits. The value produced by the system is primarily the journal, the engineering experience, and the calibration data. P&L is secondary.

**Operator-gated.** Every state transition that increases risk requires explicit operator approval. Agents recommend; the operator decides. No autonomous promotion of capital allocation.

**Indefinite horizon.** No countdown, no competition, no deadline. HERON is designed to run for years. A graceful wind-down is an acceptable outcome at any time.

### 1.3 Baseline Comparison

For every LLM-gated strategy, HERON runs a deterministic-only variant of the same strategy in parallel during paper trading. The deterministic variant uses identical entry/exit rules, sizing, and universe, but does not consult the LLM for candidate selection or conviction. **A strategy may only be promoted to live if its LLM variant statistically outperforms the deterministic baseline after costs over 90 market days.** Without this, HERON cannot distinguish LLM value from decorated noise.

---

## 2. Goals and Non-Goals

### 2.1 Goals

- Propose trading strategies from news, market patterns, and operator input, with supporting rationale.
- Paper-trade every approved strategy in parallel with a deterministic baseline variant.
- Recommend paper-to-live promotion only when the LLM variant beats the baseline after costs per the statistical test in Section 10.2.
- Execute live trades deterministically, with hard risk limits enforced in code.
- Produce a journal and dashboard that make every decision legible and accumulate a calibration dataset of (signal → rationale → outcome) triples.
- Operate within a $45/month cost ceiling.
- Track and enforce wash-sale exposure, PDT/GFV predicates, and idempotent order submission in code before any order is sent.

### 2.2 Non-Goals

- Beating the S&P 500 or any benchmark at the $500 scale.
- High-frequency or millisecond-sensitive trading.
- Derivatives, leveraged products, or shorting.
- Fully autonomous graduation of strategies to live capital.
- Meaningful monthly income at the $500 scale.
- Day-trading strategies that would require PDT exemption or a margin account above $25,000.

---

## 3. Strategy Lifecycle

A strategy is the first-class object of HERON. The state machine is deliberately simple for Phase 1; it will expand when HERON is running 3 or more strategies concurrently.

```
PROPOSED → PAPER → LIVE → RETIRED
```

| State | Description | Transition Trigger |
|---|---|---|
| PROPOSED | Research agent has surfaced a strategy hypothesis with rationale. Sitting in operator's approval queue. | Operator approval |
| PAPER | Strategy and its deterministic baseline variant are both running on paper. Both equity curves tracked independently. | Operator approval + baseline-beat test (Section 10.2) passes |
| LIVE | Strategy allocated real capital from the seed pool. Deterministic variant continues on paper as ongoing control. | Operator or auto-retirement |
| RETIRED | Strategy stopped by operator, drawdown breach, baseline-beat failure, or broken proposals. | Terminal (reversible with operator action) |

### 3.1 Strategy Object Contents

- Human-readable description and rationale written by the proposing agent.
- Configuration: entry rules, exit rules, stop-loss and take-profit methodology, position sizing formula, universe of instruments.
- Risk budget carved from the global pool, expressed as maximum concurrent capital allocation.
- Paper equity curve, deterministic-baseline equity curve, live equity curve (when applicable), trade log, performance metrics.
- Wash-sale exposure report updated nightly.
- Current state and state history.

### 3.2 Automatic Retirement Triggers

- Ninety paper market days elapsed and the LLM variant has not beaten the deterministic baseline per the test in Section 10.2.
- Thirty or more consecutive calendar days without closing a trade (stale strategy).
- Paper or live drawdown exceeding the strategy's configured budget.
- No valid proposals for three consecutive research cycles (broken strategy).
- Any wash-sale violation the pre-trade checker failed to catch.

Auto-retirement always notifies the operator and is reversible with explicit operator action.

### 3.5 Campaigns

A **campaign** is the first-class container above strategies — the unit of paper-trading experiment. A campaign owns the 90-day paper window clock, the capital allocation pool, and the graduation lineage that lets a successful experiment promote into live.

States: **DRAFT → ACTIVE → PAUSED → GRADUATED → RETIRED**. The clock starts on the DRAFT→ACTIVE transition (`started_at`). Strategies attach to a campaign via `strategies.campaign_id`; multiple variants of the same hypothesis (LLM and deterministic baseline) belong to the same campaign so their equity curves are scored against one shared window.

Campaign object contents:
- Name, description (the hypothesis), mode (paper/live).
- Capital allocation in USD, paper-window length in days.
- `started_at`, `graduated_at`, `retired_at`, `parent_campaign_id` (set when graduating spawns a follow-on campaign).
- State-transition log preserves operator intent and reasons.

The dashboard (`/campaigns`, `/campaign/<id>`) shows day-N progress and lets the operator transition state. The supervisor's executor cycle iterates ACTIVE campaigns first, then their PAPER/LIVE strategies — pause a campaign and all its strategies stop submitting candidates without touching their individual states.

---

## 4. System Architecture

Five layers with strict interfaces. Each layer can be swapped or rewritten without touching the others. The LLM is never in the execution hot path.

| Layer | Responsibility | Implementation |
|---|---|---|
| Data | Fetch and cache market data (IEX tier), news, filings. Sanitize scraped text as adversarial input. | Python; Alpaca Data API + curated RSS |
| Research | Run LLM passes; propose strategies; generate candidates. Local model filters, API model reasons. | Ollama (Qwen 2.5 7B) + Claude API |
| Strategy | Validate candidates; compute entries/stops/targets; per-strategy risk; wash-sale and PDT pre-checks. | Pure Python, deterministic, backtestable |
| Execution | Submit idempotent orders, manage fills, enforce global risk limits, reconcile nightly. | Pure Python; broker-adapter pattern |
| Journal & Dashboard | Persist every decision; serve web UI; show LLM hit-rate alongside every approval request. | SQLite (WAL) + Flask + HTMX + Tailscale |

### 4.1 Data Layer

**Free Alpaca market data is IEX-only, covering roughly 2–3% of consolidated US equity volume.** On a sample day for AAPL, IEX showed 12,630 trades versus 535,136 on full SIP. Quotes appear wider than true NBBO, and fills clearing at better prices elsewhere look like unexplained slippage. SIP at $99/month is 20% of capital and is out of scope. HERON is designed to live inside this constraint.

Implications baked into the Strategy layer:

- Minimum-liquidity filter: reject tickers with average daily volume under $10M or price under $5.
- Bid-ask spread buffer: assume effective round-trip cost of at least 25 basis points for any same-day strategy.
- Stale-quote kill switch: never submit an order when the last successful quote is older than 10 seconds.

#### 4.1.1 News Sources and Credibility Weights

Every scraped document is treated as potentially adversarial input — stripped, sanitized, and never passed raw into an LLM system prompt. Indirect prompt injection via invisible text in scraped PDFs and filings is a documented attack surface on LLM trading agents.

| Source | Weight | Feed URL / Notes |
|---|---|---|
| SEC EDGAR 8-K filings | 1.0 | `sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom`. Material corporate events. Requires User-Agent header. |
| SEC EDGAR 10-Q / 10-K filings | 1.0 | Same pattern, `type=10-Q` / `type=10-K`. Quarterly and annual fundamentals. |
| Federal Reserve press releases | 1.0 | `federalreserve.gov/feeds/press_all.xml`. FOMC decisions and commentary. |
| US Treasury press releases | 1.0 | `home.treasury.gov/rss` |
| BLS press releases | 0.9 | `bls.gov/feed/news_release/rss.xml`. CPI, jobs report, PPI — market-moving. |
| Reuters Business | 0.8 | Reuters RSS where available; otherwise rely on Alpaca News aggregation. |
| Alpaca News API | 0.8 | Already in-stack, free, aggregates AP and Benzinga for breadth. |
| SEC EDGAR Form 4 (insider trades) | 0.7 | `type=4`. Insider buys/sells on watchlist names. |
| Seeking Alpha Market Currents | 0.4 | `seekingalpha.com/market_currents.xml`. High volume, variable quality, flagged as aggregator. |

The Data layer pulls OHLCV candles at configurable timeframes (5m, 15m, 1h, 1d). Pulls news per the table above. Deduplicates across sources. Caches aggressively — candles and filings are immutable, so a local SQLite cache becomes the source of truth after first fetch. **All SEC EDGAR requests must include a User-Agent header identifying HERON and a contact email, or SEC rate-limits the requester.**

### 4.2 Research Layer

The Research layer runs on a schedule, not in the trade execution hot path. Three scheduled passes per trading day:

- Pre-market pass (06:30 ET). Reviews overnight news, surfaces tradeable developments, generates candidates for active strategies, and less frequently proposes new strategies.
- Midday refresh (12:30 ET). Re-checks top candidates, updates conviction if conditions changed, adds new candidates if significant news broke.
- End-of-day debrief (16:30 ET). Reviews the day's trades, writes prose explanations of outcomes, publishes summary to Discord.

#### 4.2.1 Model Strategy

**Local tier: Qwen 2.5 7B Instruct (Q4_K_M quantization) via Ollama.** Research converges on Qwen 2.5/3 8B and Llama 3 8B as the two viable options for financial text classification. Qwen edges ahead on noisy data (news headlines) and structured JSON output reliability, which HERON needs. Apache 2.0 license is cleaner than Llama's community license. Roughly 0.69–0.70 macro-F1 on financial sentiment with no fine-tuning. The model does classification only — never sizing, risk, or final theses.

**API tier: Claude.** Sonnet for shortlist thesis writing, conviction scoring, strategy proposals, end-of-day debrief prose, monthly review synthesis. Haiku for cheap batch tasks. Hard daily token budget enforced in code.

#### 4.2.2 Local vs. API Routing

**Local (Ollama, free, forced JSON output):**

- News headline relevance classification.
- Sentiment classification (positive/negative/neutral).
- First-pass deduplication across sources.
- Routine summarization where a failure is low-cost.

**API (Claude):**

- Shortlist thesis writing and conviction scoring.
- Strategy proposals and their deterministic-baseline variant specifications.
- Sampled audit comparisons (post-cutoff data only).
- End-of-day debrief prose.
- Monthly review synthesis.

#### 4.2.3 Memorization Warning

Any LLM analysis of data from before the model's knowledge cutoff is contaminated by memorization and must not be treated as out-of-sample evidence. Lopez-Lira, Tang, and Zhu (2025) demonstrate that explicit instructions to respect cutoff dates fail — models still achieve recall-level accuracy on pre-cutoff data. A replication on the widely-starred AI Hedge Fund repo showed returns dropping by roughly 22 percentage points when testing shifted from pre-cutoff to post-cutoff periods. HERON treats all pre-cutoff backtests as reference only.

### 4.3 Strategy Layer

Pure Python, no LLM in the hot path. Consumes candidates from the Research layer for active strategies and decides whether, when, and how much to trade.

- Validate candidate freshness. Stale candidates discarded.
- Compute entry, stop, and target from structural levels (ATR multiples, prior swings) — not from LLM-suggested numbers.
- Size positions based on strategy risk budget, account equity, current exposure, and a dynamic minimum-expected-profit threshold (default floor: 30 bps edge after costs).
- Wash-sale pre-check (Section 5.4). Reject any entry that would trigger a disallowed loss.
- PDT/GFV pre-check (Section 5.5). Reject any entry that would trip pattern day trading or good faith violation rules.
- Enforce per-strategy concurrent-position and concentration limits.
- Decide when to close, trim, or hold based on rule-based triggers.

Because the LLM is outside the hot path, the Strategy layer is deterministic and reproducible. A historical day can be replayed and will produce the same result.

### 4.4 Execution Layer

Talks to the broker. Submits orders, listens for fills, handles rejections and retries. Broker-adapter interface allows future adapters to share the same Strategy-layer API.

#### 4.4.1 Fractional Share Constraints

At $500 with 3 concurrent positions, HERON trades fractional shares. Alpaca imposes hard constraints: **fractional orders do not support bracket or OCO orders, cannot be replaced (only canceled and resubmitted), and are day-only (TIF must be DAY).** Stop-loss and take-profit logic lives in HERON's own polling code, not in Alpaca order types. The current supervisor runs executor cycles every 5 minutes during regular hours; any move toward a shorter stop-poll cadence should update runtime configuration, tests, and docs together.

#### 4.4.2 Idempotency

Every broker order carries a deterministic, opaque `client_order_id`. Entries use `make_entry_order_id(strategy, candidate_id, ticker, side)` and exits use `make_close_order_id(strategy, trade_id, ticker, side)`. On any network error, retry, or ambiguous response, the executor first queries Alpaca's orders endpoint by this ID before resubmitting. HTTP 422 *client_order_id must be unique* is the correct signal that a previous retry already succeeded, not an error.

#### 4.4.3 Reconciliation

Reconciliation runs at market open and close. Compares SQLite state (open positions, open orders, cash balance) against Alpaca's `/v2/orders`, `/v2/positions`, and `/v2/account`. Any drift must be operator-visible and block new live entries until the broker/journal mismatch is resolved.

#### 4.4.4 Other Constraints

- Margin account with PDT enforcement is the Alpaca default. HERON assumes margin+PDT and designs around it.
- No shorting, no options, no leveraged products.
- Actual fill price recorded against requested; slippage logged but not used as retroactive close trigger.
- Hard shutdown: on operator signal or unrecoverable error, flatten all positions, exit cleanly.
- Never submit orders when last quote is older than 10 seconds. Alpaca incident gating remains pre-live hardening until wired into the execution path.

### 4.5 Journal and Dashboard

Every decision at every layer writes to the journal. Proposed strategies. Candidates generated, escalated, rejected. Trades sized, filled, closed. The journal is the product as much as the trades are.

The dashboard is a Flask + HTMX web application running on the operator's workstation and accessed locally or over a Tailscale VPN. Authentication is handled by the VPN boundary; no public internet exposure. SQLite runs in WAL mode and rsyncs to cloud storage hourly for disaster recovery.

Dashboard views:

- Today at a glance — active strategies, current positions, unrealized P&L, next scheduled research pass, PDT day-trade counter, wash-sale exposure summary.
- Strategy inbox — proposed strategies awaiting operator approval, with full rationale.
- Strategy portfolio — every active strategy with its own equity curve, its deterministic-baseline curve beside it, trade log, state.
- Candidate queue — what the research layer surfaced and what the Strategy layer did with each. Every approval request displays the LLM's hit-rate on its last 50 suggestions, so operator calibration drift is visible and the queue cannot become a rubber stamp.
- Trade log — every executed trade with thesis, outcome, end-of-day prose.
- System health — last successful data fetch, last research pass, Alpaca status, error log, cost-to-date against monthly budget, last reconciliation result.
- Monthly review queue — prompts for the required go/no-go review.

Discord push alerts for time-sensitive events: daily debrief summaries, new strategy proposals, promotion recommendations, cost-ceiling warnings, hard-cap trips, reconciliation drift, monthly review reminders. Rate-limited at one alert per category per 10 minutes.

---

## 5. Risk Management

Risk is enforced at three levels: per-strategy, global, and regulatory (wash-sale and PDT).

### 5.1 Global Limits

| Rule | Limit | Enforcement |
|---|---|---|
| Max total exposure across all strategies | 80% of equity | Execution layer rejects new entries |
| Max concurrent positions system-wide | 3 (raised to 6 only above $1,500 equity) | Execution layer rejects |
| Max single-trade loss | 5% of equity | Hard stop on every trade |
| Max daily loss (all strategies) | 8% of equity | Halt new entries for the day |
| Max daily new entries | 3 | Forces selectivity; executor rejects beyond cap |
| PDT day-trade count (rolling 5 business days) | 3 (of 4 FINRA limit) | Strategy layer rejects same-day-close entries at cap |
| Wash-sale lookback window | 30 days | Strategy layer rejects repurchase of recent losers and substantially-identical peers |
| Paper-to-live transition | Operator approval + baseline-beat | No automatic trigger |
| Monthly review gate | Required written go/no-go | Blocks new promotions if missed |

### 5.2 Per-Strategy Limits

Each strategy declares its own limits in configuration, within or stricter than the global limits:

- Max capital allocation (as percentage of total equity).
- Max concurrent positions for this strategy.
- Drawdown budget before auto-retirement.
- Minimum conviction for a candidate to be considered.
- Minimum holding period (to preserve PDT budget).

### 5.3 Cap-and-Fallback Pattern

Every hard cap names its fallback behavior. No orphan limits with undefined consequences.

| Cap | Immediate Action | Recovery Condition |
|---|---|---|
| Monthly API cost projected to exceed $45 ceiling | Halt Research layer; Strategy layer continues managing open positions | Operator resets budget or month rolls over |
| Max daily loss reached | Halt new entries for the day; manage open positions | New trading day |
| Max total exposure reached | Reject new entries across all strategies | Exposure falls below cap |
| Strategy drawdown budget reached | Auto-retire the strategy | Operator re-approves |
| PDT limit would be tripped by next close | Reject any entry requiring same-day exit | Rolling window rolls off |
| Wash-sale exposure detected pre-trade | Reject the entry; log disallowed-loss amount | 30-day window closes |
| Local model unresponsive | Alert; fall back to rules-only candidate generator | Operator toggles research back on |
| Data feed failure or Alpaca incident | Alert; freeze new entries, manage open positions | Data feed recovers and quote age < 10s |
| Reconciliation drift detected | Halt new live entries; alert/operator-visible event | Operator resolves broker/journal mismatch and reruns startup audit; dedicated acknowledge/resume UI is follow-up work if needed |

### 5.4 Wash-Sale Tracking

**This is the single most important regulatory risk in HERON.** IRC §1091 disallows loss deductions when a substantially-identical security is repurchased within a 61-day window (30 days before, sale day, 30 days after). A bot that trades the same tickers repeatedly can generate phantom taxable income: disallowed losses add to the replacement lot's basis, and if crossed over year-end, the deferred loss may fail to net against realized gains. Documented worst-case patterns for retail algo traders include net annual P&L near $30,000 becoming a taxable gain of roughly $225,000 after wash-sale adjustments.

HERON's defenses:

- Ticker-family map in config, reviewed quarterly.
- Pre-trade check. Before any entry, Strategy layer queries the journal for any closed losing lot in the same family within the last 30 days. If present, entry rejected and disallowed-loss amount logged.
- Post-trade annotation. On every sale at a loss, journal records loss amount and 30-day window end date.
- Nightly exposure report on dashboard, with year-end approach warnings flagged.
- Cross-broker limitation disclosed. Alpaca's 1099-B only catches wash sales within Alpaca. Trades of same securities in other accounts (taxable brokerage, spouse's, IRA) are operator's responsibility.
- Section 475(f) mark-to-market election explicitly out of scope at $500.

#### 5.4.1 Initial Ticker-Family Map

- `{SPY, VOO, IVV}` — S&P 500 trio, substantially identical.
- `{QQQ, QQQM}` — Nasdaq-100 pair.
- `{IWM, VTWO}` — Russell 2000 small-cap.
- `{DIA}` — Dow 30 (no common substitute on watchlist).
- `{XLF}`, `{XLE}` — sector ETFs, each its own family.
- Individual stocks (AAPL, MSFT, GOOGL, AMZN, NVDA, META) — each its own family.

### 5.5 PDT and Settlement Mechanics

**Alpaca's default account type is a margin account, and margin accounts under $25,000 are subject to FINRA's pattern day trading rule.** Four or more day trades within a rolling five business days trips PDT. Alpaca's own PDT protection rejects offending orders with HTTP 403. On $500, HERON effectively has three day-trades per rolling five business days.

Implications for strategy design:

- Any strategy requiring same-day exits more than three times per week is not viable.
- Strategy layer maintains a rolling 5-business-day day-trade count, updated from the journal on every entry/exit, and rejects entries that would require a fourth same-day close.
- Even with cash account access, T+1 settlement (effective 2024) creates Good Faith Violations when unsettled proceeds fund a buy that is then sold before settlement. Three GFVs in 12 months triggers a 90-day cash-up-front restriction.
- HERON prefers swing strategies (overnight to 5 days) over intraday. Strategies are tagged with a minimum holding period the Strategy layer enforces.
- FINRA filed SR-FINRA-2024-019 which may restructure PDT; HERON's PDT predicate is encapsulated so it can be updated without touching the rest of the code.

### 5.6 Deliberately Absent

- Multi-tier drawdown escalation with graduated restrictions.
- Automatic conviction-score recalibration from small trade samples.
- Kill switches triggered purely by drawdown percentage. The kill switch is the operator, the monthly review, or account-zero.
- Retroactive close-on-slippage rules.
- Shorting, options, margin beyond PDT constraints.

---

## 6. LLM Audit Strategy

Four parallel audit mechanisms:

**Baseline comparison (primary).** Every LLM-gated strategy runs alongside a deterministic-only variant. Divergence between the two equity curves over 90 days is the primary signal of whether the LLM is adding value.

**Cost-triggered audit.** Every local-model decision that led to a losing trade is flagged for post-mortem. The post-mortem records local-model output, what Claude would have produced on escalation (post-cutoff data only), and actual outcome. Divergence between local and escalated on losing trades is a drift signal.

**Continuous sampling.** Approximately 15% of local-model decisions are randomly sampled and escalated to Claude for comparison, logged regardless of outcome. Catches drift that isn't correlated with losses.

**Memorization guard.** All retroactive comparisons restricted to data strictly after the model's knowledge cutoff. Pre-cutoff comparisons are flagged reference-only and excluded from trust-score calculations.

All audit streams feed a local-model trust score displayed on the dashboard. If the score drops below threshold, the operator may raise the escalation rate, reduce local-model scope, or swap the model.

---

## 7. Cost Envelope

**Monthly ceiling: $45.** Enforced in code; Research layer halts new escalations when month-end projection exceeds ceiling.

Allocation:

- Claude API inference: ~$31.50/month (70%).
- Infrastructure (Tailscale free tier, Discord free, VPS if needed): ~$9/month (20%).
- Buffer: ~$4.50/month (10%).

The Strategy and Execution layers continue operating on existing candidates and open positions when cost halts research — HERON does not stop trading when it can't research. Cost-to-date, projection, and per-strategy attribution are visible on the dashboard.

---

## 8. Watchlist

Phase 1 uses a fixed operator-curated watchlist. Dynamic watchlist management returns once HERON is running 3 or more strategies concurrently.

### 8.1 Core Watchlist (12 tickers)

**Mega-cap tech (6):** AAPL, MSFT, GOOGL, AMZN, NVDA, META.

**Broad-market ETFs (4):** SPY, QQQ, IWM, DIA.

**Sector ETFs (2):** XLF (financial), XLE (energy).

*Rationale:* The six mega-cap tech names are correlated and would otherwise concentrate HERON into one regime. XLF and XLE provide non-tech exposure so the Research layer has something useful to do when tech is flat. All 12 satisfy the liquidity filter (ADV > $10M, price > $5). ETFs are for context and sector-rotation strategies in later phases; the PEAD reference strategy trades only the six individual stocks.

---

## 9. Reference Strategy: Post-Earnings Announcement Drift (PEAD)

PEAD is one of the most durable market inefficiencies in academic literature (Bernard & Thomas 1989, persists through 2020s). HERON's first strategy because: **(1)** PDT-safe — 5–10 day holds use ~0 day-trades. **(2)** Deterministic trigger — earnings surprise percentage is rule-based, no LLM needed for the signal. **(3)** Natural baseline — LLM variant can filter via news context; deterministic variant trades every qualifying surprise. **(4)** Teaches position sizing, ATR stops, surprise-magnitude thresholds, and post-earnings volatility dynamics.

### 9.1 Specification

| Parameter | Value |
|---|---|
| Universe | AAPL, MSFT, GOOGL, AMZN, NVDA, META (ETFs excluded — no earnings) |
| Trigger | Earnings surprise ≥ 5% on consensus EPS, announced within last 24h |
| Entry | Next session open at market order |
| Stop | 2× 14-day ATR below entry price |
| Target | 3× 14-day ATR above entry, or time-exit at market close on day 10 |
| Position size | 15% of equity per position; max 3 concurrent |
| Minimum hold | 2 trading days (PDT safety) |
| Deterministic variant | Enters every qualifying surprise |
| LLM variant | Same entry/exit rules; LLM filters via 8-K context and guidance language ('raised guidance', 'beat every segment', 'guided down despite beat'). Can veto low-quality beats. |

---

## 10. Graduation and Baseline-Beat Test

### 10.1 Paper Window

Every strategy runs 90 market days on paper before any live-promotion recommendation. Twenty days (as suggested in early drafts) is not enough trades to distinguish signal from noise.

### 10.2 Baseline-Beat Test

**Statistical test: paired bootstrap 95% confidence interval on daily return differences (LLM variant minus deterministic variant) excludes zero.**

Procedure:

- At day 90, compute paired daily returns: for each market day, the LLM variant's return and the deterministic variant's return on the same universe.
- Compute day-by-day return difference `d_i = r_LLM,i − r_baseline,i`.
- Bootstrap-resample the `d_i` vector 10,000 times with replacement; compute the mean of each resample.
- Construct the 95% confidence interval from the 2.5th and 97.5th percentiles of the bootstrap means.
- If the entire CI is above zero, the LLM variant has beaten baseline at the 95% level. Eligible for live promotion, operator approval still required.
- If the CI spans zero or is below, the strategy retires the LLM role (reverts to deterministic-only journaling) or the whole strategy retires.

*Why this test:* PEAD produces roughly 12–30 trades across six names in 90 days. A 99% CI requires more power than this sample provides; a raw 5% equity-curve gap is too path-dependent. The paired bootstrap handles small samples honestly, respects the fact that both variants face the same daily market conditions, and makes no parametric assumptions about return distributions.

### 10.3 Additional Graduation Signals

Required alongside baseline-beat:

- Paper equity curve net-positive after realistic commission and slippage simulation.
- Drawdown within the strategy's configured budget.
- No operator-intervention-required failures during the paper period.
- No wash-sale violations missed by the pre-trade checker.

Ready-not-ready judgment signals:

- Paper performance driven by one or two outlier trades → not ready.
- Drawdowns approaching configured budget → not ready.
- Behavior the operator cannot explain from the journal → not ready.

---

## 11. Monthly Review Gate

On the first trading day of each calendar month, the dashboard prompts for a written go/no-go review covering:

- System health and stability.
- Per-strategy performance including baseline-beat status.
- Cost vs. $45 budget.
- Wash-sale exposure and PDT usage in the prior month.
- Any kill criteria (Section 14) triggered.
- Explicit go/no-go decision with rationale.

Until filed, no new strategies can be promoted. Existing strategies continue operating normally.

---

## 12. Backtesting

The Strategy layer must be backtestable before it places a live order.

- Replay any historical period deterministically; identical results on repeated runs.
- Include delisted tickers at point-in-time universe membership (survivorship-bias defense).
- Apply news at actual publication timestamp, not bar open (look-ahead defense).
- Simulate commission, SEC Section 31 fees, FINRA TAF, and realistic slippage (half the prevailing spread plus 5 bps).
- Support synthetic candidate streams from historical news for research-layer-dependent strategies.
- Produce the same metrics the dashboard shows for paper and live, so all three modes are directly comparable.
- Support parameter sweeps to identify robust parameter ranges, not overfit ones.
- Flag all backtest windows overlapping the LLM's training data as memorization-contaminated.

Every strategy proposal that reaches PAPER state is first run through the backtester over a defined historical window. The backtest report attaches to the strategy object and is visible in the dashboard before paper trading begins.

---

## 13. Infrastructure

**Host.** Operator's main workstation, selected for GPU (local LLM inference) and always-on availability during working hours.

**Supervisor.** systemd on Linux or equivalent service manager. Owns the bot process, restarts on crash. Bot performs a startup audit every launch: reconciles open positions against broker, verifies every open position has a protective stop (or polled virtual stop for fractionals), restores strategy state from journal, resumes scheduled research passes.

### 13.1 Stale-Data Kill Switch

The implemented kill switch rejects orders when the last successful quote is older than 10 seconds. Alpaca status-page incident gating is a pre-live hardening item; until wired, operators should treat broker/API incident checks as manual operational readiness. Third-party monitors logged more than 60 Alpaca data-endpoint outages in a recent four-month window. Scheduled maintenance runs the second Saturday of each month, 9:00–11:30 ET.

### 13.2 Corporate Actions

Planned pre-live hardening: poll Alpaca's `/v2/corporate_actions/announcements` endpoint nightly. Splits, reverse splits, and dividends should be recorded in the journal and applied to stop-loss and take-profit levels overnight. Delistings and mergers should flag the position for operator review next morning and block new entries in the affected ticker.

### 13.3 Secrets and Backup

- API keys (Alpaca, Claude, Discord webhook) in OS keychain or `.env` file with 0600 permissions. Never in repo. Never in logs. Separate keys for paper and live.
- SQLite in WAL mode. Hourly rsync to cloud storage.
- All timestamps stored in UTC. Display converts to America/New_York. NTP sync enforced at OS level.
- Supervisor handles workstation sleep and reboot events. Bot saves state on shutdown signal, restores on startup, uses journal as single source of truth for in-flight work.

### 13.4 Network and Alerts

- Dashboard bound to localhost by default, exposed to operator devices via Tailscale VPN. No public internet exposure.
- Discord webhook for push notifications. Rate-limited at one alert per category per 10 minutes.
- Optional: Sentry free tier for exception capture (5,000 events/month).

### 13.5 Technology Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Local LLM runtime | Ollama |
| Local LLM model | `qwen2.5:7b-instruct-q4_K_M` |
| API LLM | Claude Sonnet (primary), Haiku (cheap batch) |
| Broker | Alpaca, margin account with PDT enforcement |
| Market data | Alpaca Data API, IEX tier (free) |
| News | Alpaca News + curated RSS (Section 4.1.1) |
| Storage | SQLite in WAL mode |
| Trading calendar | `exchange_calendars` or `pandas_market_calendars` |
| Web framework | Flask + Jinja + HTMX |
| Charts | Plotly or lightweight-charts |
| Config | YAML |
| Scheduling | APScheduler (in-process) |
| Supervisor | systemd or equivalent |
| Remote access | Tailscale VPN |
| Alerts | Discord webhook |
| Observability (optional) | Sentry free tier |

---

## 14. Kill Criteria

Hard triggers, not soft prompts:

- Ninety paper days with the LLM variant failing the baseline-beat test (Section 10.2) → strategy retires. LLM role reduced to journaling and debrief prose only for that strategy; Research layer's proposal function disabled for it.
- Two consecutive months of unread end-of-day debriefs → system auto-pauses all new entries; requires explicit operator reactivation.
- Monthly costs exceeding $75 for two consecutive months → Research layer disabled until manual re-enable.
- Any wash-sale violation the pre-trade checker failed to catch → immediate halt on all strategies, full journal audit, manual re-enable only.
- Three or more consecutive weeks where HERON maintenance work dominates new feature work → signal to stop adding features and consider retiring the project gracefully.
- Flat-to-negative equity after 30+ paper days on every active strategy simultaneously → prompt in monthly review for explicit continue/wind-down decision.

A graceful wind-down is an acceptable outcome. HERON is a learning system; the write-up of why it was wound down is itself a valuable artifact.

---

## 15. Implementation Roadmap

Each milestone is independently demo-able. No milestone requires the next to be valuable on its own.

1. **Data layer.** Fetch and cache OHLCV + news from Alpaca (IEX) and the Section 4.1.1 RSS sources with per-source tagging, dedup, and adversarial-input sanitization. *Demo:* CLI prints today's bars and headlines for the watchlist.
2. **Journal and SQLite schema.** Full schema: strategies, proposals, candidates, trades, wash-sale lots, PDT day-trades, audits, costs, reviews. WAL mode, hourly backup configured. *Demo:* insert fake records, query them, render a plain HTML table.
3. **Strategy framework skeleton.** Implement PROPOSED → PAPER → LIVE → RETIRED state machine and per-strategy config objects. *Demo:* create a strategy, move through states, see history.
4. **Strategy layer** with wash-sale and PDT pre-checks, for the PEAD reference strategy. Hardcoded rules. *Demo:* feed synthetic candidates, see order intents or rejections with reasons including wash-sale and PDT blocks.
5. **Execution layer** with Alpaca paper adapter. Idempotent `client_order_id`, polled virtual stops for fractional orders, startup audit, nightly reconciliation. *Demo:* end-to-end synthetic candidate becomes a paper order and journal entry; kill process and restart to prove idempotency.
6. **Dashboard v1.** Strategy portfolio view with baseline-beat chart pair, trade log, equity curves, system health, PDT counter, wash-sale exposure, monthly review prompt, LLM hit-rate display. *Demo:* operator opens URL, sees active strategies and today's activity.
7. **Research layer — local only.** Ollama (Qwen 2.5 7B) produces candidates from news and price context for PEAD. Forced JSON output. *Demo:* morning pass runs; candidates appear in queue.
8. **Research layer — Claude escalation.** Shortlist escalates for thesis writing and conviction scoring. Per-day token budget enforced. *Demo:* journal shows local score, escalated score, final disposition.
9. **Baseline-variant runner.** Every paper strategy runs alongside its deterministic-only twin. Dashboard shows both curves side by side. *Demo:* 5-day paper run shows two equity curves.
10. **Strategy proposal flow.** Research layer proposes new strategies; operator approves in dashboard. *Demo:* agent proposes a strategy from news, operator approves, strategy enters PAPER with its baseline variant.
11. **Audit system.** Cost-triggered post-mortems and continuous sampling (post-cutoff only), with local-model trust score on dashboard.
12. **End-of-day debrief and Discord alerts.** Claude writes prose; Discord pushes summary and actionable events. *Demo:* daily summary arrives in Discord with dashboard link.
13. **Backtester.** Replay historical periods with point-in-time news and delisted-ticker universe, parameter sweeps, reports attached to strategy objects, memorization-contamination warnings. *Demo:* run backtest twice, get identical results.
14. **Cost controls.** Hard cap with halt-research fallback; dashboard cost tracking. *Demo:* simulated cost overrun triggers graceful halt.
15. **Resilience hardening.** Supervisor, startup audit, reconciliation, crash recovery, secrets management. *Demo:* kill process mid-trade, restart, verify clean state.
16. **90-day paper trading window for PEAD.** Mandatory. Graduate to live only on operator approval and only if the baseline-beat test passes.

---

## 16. Future Scope (Not in Scope Now)

Deferred until HERON has been running at least 6 months in Phase 1 and operational experience teaches what actually matters:

- Strategy Promoter agent. Reviews paper performance and baseline-beat outcomes on a configurable cadence and produces paper-to-live recommendations. Operator still approves.
- Multi-agent review pipeline. Generator, critic, risk-check, and decider agents replace the single-LLM pattern, with disagreement tracking.
- Crypto execution adapter. Note: crypto wash-sale rules apply starting tax year 2025.
- Cross-asset correlation awareness.
- Dynamic watchlist management.
- Full six-state strategy lifecycle (reintroduced when 3+ strategies are live).

---

## 17. Risk Acknowledgments

- LLM alpha has not been empirically demonstrated net of costs for retail traders. Published academic Sharpes have decayed rapidly as LLMs diffused. HERON treats LLM outperformance over its deterministic baseline as the thing to be proven, not assumed.
- Memorization contamination. LLMs achieve recall-level accuracy on pre-cutoff data even when instructed otherwise. Pre-cutoff backtests are reference-only.
- Small-account economics. At $500, fees and slippage are meaningful. IEX-only data (2–3% of consolidated volume) makes slippage worse.
- Wash-sale rule risk. Section 5.4 is the defense; its failure is an automatic-retirement trigger.
- PDT exposure. Margin account default. Three day-trades per rolling five business days.
- Prompt injection via scraped content. Section 4.1 sanitizes; the operator should still assume poisoned news is in the threat model.
- Local model quality. Qwen 2.5 7B is a competent filter but not a decision-maker on numerical reasoning.
- Operator attention. Mitigated by Discord push alerts, LLM hit-rate display, monthly review gate.
- Realism of any income goal. Microstructure, tax mechanics, and account-size economics dominate. HERON's primary value is learning, engineering, and the journal.

---

## 18. Appendix: Bootstrap Setup

One-time environment setup:

1. **Install Ollama.** Download from `ollama.com/download`. No account required.
2. **Pull the local model.** `ollama pull qwen2.5:7b-instruct-q4_K_M` (≈4.7 GB, 5–15 min).
3. **Smoke test.** `ollama run qwen2.5:7b-instruct-q4_K_M "Classify: 'Apple beats Q3 earnings.' Reply JSON: {sentiment, relevance}"`. Expect clean JSON in under 2 seconds.
4. **Optionally benchmark Llama 3.1 8B** against Qwen on 100 hand-labeled headlines. Keep whichever wins by ≥3 F1 points. Tie goes to Qwen (Apache 2.0, JSON reliability).
5. **Pin exact model tag** in YAML config to prevent silent updates.
6. **Create Alpaca paper account** at `alpaca.markets`. Generate paper API keys. Separate live keys only when the first strategy graduates.
7. **Create Claude API key** at `console.anthropic.com`. Set a hard spend limit of $45/month in the console as a belt-and-suspenders layer above HERON's own token budget.
8. **Set up Discord webhook** in a private server. Store webhook URL in `.env` with 0600 permissions.
9. **Install Tailscale** on workstation and phone. Tag the workstation for remote dashboard access.
10. **Register a User-Agent** for SEC EDGAR requests (required):

    ```
    HERON-research contact@yourdomain.com
    ```

---

*— End of Document —*
