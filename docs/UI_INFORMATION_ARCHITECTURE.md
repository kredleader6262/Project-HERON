# UI Information Architecture

Canonical source: [Project-HERON-v4.md](../Project-HERON-v4.md). The dashboard target is the **HERON Flight Deck**: a control panel for a trading system where the operator must always know what is running, what is safe, what needs attention, and what is about to happen.

The visual brand direction is **Executive Signal**: a premium financial-operator desk aesthetic — dark, composed, decisive, high-trust, signal-focused. Brand phrase: *Insight. Strategy. Advantage.*

Visual implementation happens in Stage 6 or later. This doc defines information architecture, named patterns, and the Executive Signal design language. It does not contain final pixel mockups.

## Top-Level Navigation

Navigation order is fixed:

1. Mission
2. Desks
3. Approvals
4. Activity
5. Portfolio
6. System

## Mission

Mission is the operator cockpit. It contains the persistent Global Safety Strip and cards for active desks, pending approvals, live runs, today's P&L, alerts/exceptions, and system health.

Mission answers: what is running, what is blocked, what needs approval, is the account safe, and is the LLM beating baseline.

## Desks

Desks is the primary workspace. Desk is the operator-facing name for the existing Campaign substrate in Phase 1; internal database/service names still use Campaign.

Desk tabs:

- Overview.
- Research.
- Signals.
- Strategies.
- Backtests.
- Runs.
- Trades.
- Feedback.
- Logs.
- Settings.

The Desk Overview is the **Desk Control Board**. It is headlined by the LLM-gated equity curve vs the deterministic baseline curve using the **Baseline Ghost** pattern. Strategy Detail uses the same paired-curve treatment at strategy level.

## Approvals

Approvals is the first-class human decision queue. Approval items render as **Decision Cards**.

Decision Card fields:

- Requested action.
- Desk.
- Strategy.
- Signal source.
- LLM confidence.
- LLM hit-rate on its last 50 suggestions.
- Local LLM trust score for the relevant desk/task.
- Baseline comparison.
- 95% CI status if paper.
- Risk impact.
- PDT impact.
- Wash-sale impact.
- Cost impact.
- Rollback plan.

Decision Card actions:

- Approve.
- Reject.
- Edit.
- Send to Backtest.
- Paper Trade First.
- Request More Research.
- Pause Desk.
- Mute Signal Type.

Do not add a generic Block Similar Suggestions action in Phase 1; it is too ambiguous.

## Activity

Activity is the chronological event log rendered as the **Timeline Spine**. It aggregates events, scheduler runs, audits, trades, candidates, reviews, strategy/Desk state logs, and operator actions where available.

Filters:

- Desk.
- Strategy.
- Signal.
- Ticker.
- Agent.
- Model.
- Status.
- Approval state.
- Date.

Every transaction traces backward:

```text
Transaction <- Order <- Run <- Strategy <- Signal <- Research Finding <- Agent <- Feed <- Desk
```

Trace lineage appears inline through **Trace Chips**. Activity is a new aggregate page. Existing `/actions` remains the scheduler-control alias and is not replaced by Activity.

## Portfolio

Portfolio is money truth with attribution. It contains P&L, holdings, exposure, open orders, closed trades, performance by desk, performance by strategy, performance by signal type, baseline-vs-LLM contribution, drawdown, risk, wash-sale exposure, and PDT-relevant trades.

Every position rolls up to its Desk and Signal of origin through Trace Chips.

## System

System is explicitly subdivided to avoid becoming a junk drawer.

**Operations** includes Health, Costs, Schedules, Reconciliation, Broker status, and Monthly Review history.

**Configuration** includes API Keys, Data Sources, Policies, Risk limits, and Model settings.

**Introspection** includes Agents, Model logs, Audit logs, Prompt versions, and Local LLM trust history.

## Global Safety Strip

The Global Safety Strip is persistent on every page and cannot be hidden.

Required indicators:

- Trading / View Mode: PAPER, LIVE, or ALL.
- PDT counter.
- Wash-sale risk.
- Cost-to-date / monthly cap.
- Daily loss used.
- Exposure: gross and net.
- Buying power.
- Account reconciliation status.
- Broker/API health.
- System Safety Mode: NORMAL, SAFE, DERISK.
- Local LLM trust aggregate.
- Monthly review gate status.
- Promotion gate status.

Color and intensity shift with severity. LIVE mode increases visual emphasis. SAFE mode visibly disables unsafe controls.

## Named Patterns

### Global Safety Strip

A persistent top-of-page bar present on every page. It contains the account-level globals above and forms the always-on situational awareness layer. It cannot be hidden.

### Why Drawer

A right-side or full-width drawer opened from any decision, trade, signal, or candidate. It shows the full backward trace: evidence chain, agent decisions, model outputs, rule evaluations, and operator approvals that led to the object. The Why Drawer answers why a trade happened without forcing the operator to leave the current page.

### Baseline Ghost

The deterministic baseline equity curve drawn as a low-contrast trace behind the LLM-gated curve on every paired chart. Required on Strategy Detail, Desk Overview, and Portfolio attribution charts where baseline-beat is the operator question.

### Timeline Spine

The Activity page's chronological backbone. Events appear on a vertical or horizontal spine with filterable lanes for desk, strategy, signal, ticker, agent, model, status, approval state, and date. It unifies the scattered v3 surfaces for inboxes, audits, candidates, trades, and actions.

### Decision Cards

The visual format for entries in Approvals. Cards are dense but not cluttered. Every field listed in the Approvals section is present, while hierarchy emphasizes the approval question before metadata.

### Trace Chips

Inline pill/chip elements rendered on objects such as trades, positions, candidates, and signals. Example lineage: `Desk: PEAD` -> `Signal: post-earnings-drift` -> `Strategy: PEAD Long v1`. Clicking a chip opens that object. Trace Chips make backward traceability visible without requiring the Why Drawer for simple cases.

### Desk Control Board

The Desk Overview page format. It is headlined by paired LLM-gated vs Baseline Ghost equity curves. Below the chart: signal pipeline status, strategy stack, recent runs, recent trades, feedback recommendations, paper-day counter, and CI status if in paper.

### Mode-Aware Emphasis

PAPER and LIVE render with distinct visual emphasis using the Executive Signal palette (see Design Language below). LIVE mode increases color saturation on critical surfaces, adds visible borders, and makes the Safety Strip more prominent. SAFE mode is its own emphasis state with visibly disabled controls and bold status messaging. DERISK mode sits between LIVE and SAFE — restricted-action emphasis. Mode emphasis is achieved through token swaps, weight changes, and border emphasis, not through wholesale palette changes.

### Executive Signal Aesthetic

The HERON dashboard's visual language. Dark-foundation, layered surfaces, Signal Gold as a sparingly-used accent, restrained Heron Teal as a secondary accent, refined typography, disciplined spacing, restrained motion. Full token system and principles are defined in the Design Language section below. This is the canonical pattern name used across the v4 refactor.

## Design Language: Executive Signal

### Brand Identity

Project HERON's UI direction is **Executive Signal**. The dashboard should feel like a premium financial operator desk — calm authority, strategic intelligence, decisive presence. It should NOT feel like generic SaaS, a crypto product, ornate luxury, or a cyberpunk console.

Brand phrase: *Insight. Strategy. Advantage.*

The operator-supplied mood image (filed under Visual Reference at the end of this doc) is canonical reference for **tone, contrast, density, and palette discipline**. It is NOT a literal layout to recreate. The HERON Flight Deck has its own information architecture — the six sections above. Executive Signal supplies the visual vocabulary; the IA stays as defined here.

### Visual Principles

Dark foundation with layered surfaces. Pure black is rare; surfaces are deliberately stratified. Gold (Signal Gold) used sparingly as a focus, brand, and signal accent — not as a default action color, not as a warning color, not on every button. One restrained teal-blue (Heron Teal) accent for intelligence/observation/water moments, used in moderation. High readability for dense financial data; tabular numerals for prices, P&L, and market data. Subtle borders. Disciplined spacing. Restrained motion. Premium, but not ornate. Tactical, but not cyberpunk.

### Anti-Patterns

Do NOT:

- Turn every button, icon, or chart into gold or orange.
- Use pure black everywhere; surfaces should be layered.
- Use gold as the default warning color; warnings use amber, which is semantically distinct from Signal Gold.
- Make the dashboard look like a crypto casino, Bloomberg clone, or generic admin product.
- Overuse glow, glassmorphism, gradients, or decorative bird imagery. The heron mark is a brand asset, not a recurring design motif.

### Color Tokens

**Backgrounds:**

- `bg.root` — `#0B0B0D` (Onyx, app-shell base)
- `bg.app` — `#0F1117` (canvas under content)
- `bg.sidebar` — `#0F1B2E` (Midnight, navigation)
- `bg.panel` — `#151A21` (cards and panels)
- `bg.panelElevated` — `#1F232A` (Slate, elevated panels)

**Text:**

- `text.primary` — `#F7F8FA`
- `text.secondary` — `#B9C0CA`
- `text.muted` — `#7D8794`
- `text.disabled` — `#545B66`

**Borders:**

- `border.subtle` — `rgba(255,255,255,0.08)`
- `border.strong` — `rgba(255,255,255,0.14)`

**Brand accent (Signal Gold):**

- `signal.gold` — `#D89A2B` (primary brand)
- `signal.gold.hot` — `#F5A623` (active/focus state)
- `signal.gold.soft` — `rgba(216,154,43,0.14)` (background tint)
- `signal.gold.border` — `rgba(216,154,43,0.38)` (subtle outline)

**Secondary accent (Heron Teal):**

- `heron.teal` — `#1E5A6B`
- `heron.teal.bright` — `#2F8FA0`
- `heron.teal.soft` — `rgba(47,143,160,0.14)`

**Semantic colors:**

- Success / profit: muted green, not neon.
- Danger / loss: muted red, not harsh.
- Warning / risk: amber, used only when the signal is semantically a warning. Gold is NOT amber.
- Info: teal/blue.

Specific semantic hex values are determined in Stage 6 implementation; the rule is "muted, not neon, not harsh."

### Typography

- **Serif** — refined serif for brand and display moments only (page titles, primary headlines, the brand mark when typeset). Not for body, not for UI chrome.
- **Sans-serif** — clean sans-serif for dense UI: nav, labels, buttons, body text, table cells.
- **Tabular numerals or mono** — required for prices, P&L, market data, and any column where digits must align.

Specific typeface selection is a Stage 6 decision; the role separation (serif/sans/tabular) is canonical.

### Mode-Aware Token Application

PAPER and LIVE render with distinct emphasis using the tokens above:

- **PAPER mode** — Signal Gold appears on the brand mark and active Safety Strip indicators. Non-critical chrome stays muted on Slate and Panel surfaces.
- **LIVE mode** — Signal Gold takes on stronger presence on critical surfaces (Safety Strip, active position indicators, mode badge). `border.strong` is preferred over `border.subtle` on key panels. The operator should feel the shift unmistakably without the dashboard becoming a different product.
- **SAFE mode** — controls visibly disabled. Status messaging uses the danger semantic. Safety Strip dominates the page hierarchy.
- **DERISK mode** — restricted-action emphasis between LIVE and SAFE; uses warning amber on the affected controls only, not on the whole page.

Mode emphasis is achieved through token swaps, weight changes, and border emphasis — not through wholesale palette changes. The dashboard is recognizably the same product in every mode.

### Light Theme

Default theme is dark. A light theme is supported but secondary. Light theme is not a literal inversion of the dark tokens — it is a separate palette that preserves the same hierarchy (Onyx/Midnight/Slate become layered light surfaces; Signal Gold and Heron Teal remain accents but with adjusted contrast). Light theme tokens are deferred to Stage 6.

### Implementation Posture

Apply Executive Signal as a **design-token and component-style refactor first**, not a full product rewrite. The Phase 1 implementation order:

1. Define design tokens (colors, typography, spacing) in a single tokens module.
2. Update the reusable layout shell — sidebar, top bar, Global Safety Strip.
3. Update reusable components — cards, buttons, tables, status badges, chart containers, navigation chrome.
4. Apply Mode-Aware Token Application across the shell.
5. Only after the chrome is consistent, touch deeper workflows — Approvals, Activity, Desk Control Board, Portfolio attribution.

Do not redesign deeper workflow surfaces as part of the token refactor. Those are dedicated stages.

## Visual Reference

The operator-supplied mood image — "Palette Direction 2: Executive Signal" — is canonical reference for **tone, density, contrast, and palette discipline**. It shows:

- The HERON brand mark with serif wordmark and "Insight. Strategy. Advantage." tagline.
- Six palette tiles: Onyx, Midnight, Signal Gold, Platinum, Pure White, Slate.
- A mood statement and key impressions: decisive, premium, assured, sharp, focused.
- A sample Portfolio Summary frame demonstrating density, hierarchy, and accent restraint.

The sample frame in the mood image is **demonstrative**, not the HERON Flight Deck layout. The HERON dashboard implements the six-section information architecture defined above (Mission / Desks / Approvals / Activity / Portfolio / System), not a generic Portfolio Summary view.

Vision-capable interpretation of the mood image is required for Stage 6 implementation. The token system, typography roles, and visual principles documented above let any implementer (with or without vision capability) produce a coherent first cut that matches the Executive Signal direction.