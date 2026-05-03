---
description: "Use when: writing or modifying research layer, LLM integration, Ollama calls, Claude API calls, news processing, sentiment classification, candidate generation, strategy proposals, token budgets, cost tracking, or content sanitization."
---
# Research Layer Rules

Full specs in `Project-HERON-v4.md` Sections 4.2, 6, 7.

## LLM Routing

| Task | Model | Why |
|---|---|---|
| News relevance/sentiment classification | Qwen 2.5 7B (local, Ollama) | Free, fast, forced JSON |
| Dedup, routine summarization | Qwen 2.5 7B (local) | Low-cost failures |
| Thesis writing, conviction scoring | Claude Sonnet (API) | Reasoning quality |
| Cheap batch tasks | Claude Haiku (API) | Cost |
| Strategy proposals, EOD debrief, monthly review | Claude Sonnet (API) | Prose + reasoning |

**The local model classifies. It never sizes, risks, or writes final theses.**

## Cost Ceiling

- **$45/month hard cap.** Enforced in code.
- When projected spend exceeds ceiling: halt Research layer, Strategy+Execution continue on existing candidates.
- Track per-day token usage and per-strategy cost attribution. Dashboard displays cost-to-date and projection.

## Schedule

- **06:30 ET** — Pre-market: overnight news, candidates, occasional new strategy proposals.
- **12:30 ET** — Midday: re-check top candidates, update conviction, surface breaking news.
- **16:30 ET** — EOD debrief: review trades, write outcome prose, Discord summary.

## Adversarial Input

- All scraped text (news, filings, PDFs) is treated as adversarial. Strip, sanitize, never pass raw into system prompts.
- Prompt injection via invisible text in scraped content is a documented attack surface on LLM trading agents.
- SEC EDGAR requests require `User-Agent: HERON-research contact@yourdomain.com` header.

## Memorization

- Any LLM analysis of data **before the model's knowledge cutoff** is contaminated by memorization.
- Pre-cutoff backtests are **reference only** — excluded from trust-score calculations.
- All audit comparisons use post-cutoff data only.

## Audit

- Baseline comparison: every LLM strategy runs alongside a deterministic-only variant.
- Cost-triggered: every local-model decision leading to a losing trade gets post-mortem.
- Continuous sampling: ~15% of local decisions escalated to Claude for comparison.
- Feeds a trust score on the dashboard. Below threshold → operator may raise escalation rate or swap model.
