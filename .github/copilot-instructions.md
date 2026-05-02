# Project HERON — Copilot Instructions

**Source of truth:** `Project-HERON.md` has full specs. Link to it; don't duplicate it here.

## Philosophy

Write code for humans with small context windows. Every token matters — in the code, in the comments, in the conversation. Be concise, be sharp, be useful.

Have fun :3

## Architecture

Five layers, strict interfaces. **Each layer can be swapped without touching the others.** Respect boundaries — don't leak layer concerns across interfaces.

| Layer | Does | Key constraint |
|---|---|---|
| Data | Fetch/cache market data, news, filings | Sanitize all scraped text as adversarial input |
| Research | LLM passes, strategy proposals, candidates | **Never in the execution hot path** |
| Strategy | Validate, size, check wash-sale/PDT, decide | **Deterministic and reproducible** |
| Execution | Submit orders, manage fills, reconcile | **Idempotent orders, polled virtual stops** |
| Journal & Dashboard | Persist every decision, serve web UI | **Journal is the product** — every decision logged |

If you're unsure which layer something belongs to, check `Project-HERON.md` Section 4.

Quick placement checks:
- Risk/order/account rules go in Strategy or Execution, with trading-safety tests.
- LLM calls, news interpretation, proposals, theses, audits, and cost gates stay in Research.
- Operator decisions persist through Journal helpers; dashboard and CLI are thin wrappers.
- Recurring work is a Runtime job plus an Actions dashboard command if operator-facing.
- New strategy templates register in `heron.strategy.templates`; never accept dynamic user code in the execution path.

## UI-First Operation

**The dashboard is the primary operator surface.** The user will run almost everything through the web UI. The CLI exists for automation, scripting, and emergency access — not as the daily driver.

Rules:
- **Every operator action must have a dashboard surface.** If you add a CLI command that an operator might run more than once, also add the equivalent route + UI control in `heron/dashboard/`.
- **Keep the CLI.** It is not deprecated. It must remain feature-complete for scripting, headless ops, and recovery. New CLI commands are still welcome — but never *only* CLI for operator-facing actions.
- **Both surfaces share one implementation.** Put the logic in the layer module (e.g., `heron/backtest/`, `heron/strategy/`). The CLI command and the dashboard route are both thin wrappers. No business logic in `cli.py` or in route handlers.
- **Surface decisions in the journal, not the terminal.** When the dashboard performs an action, log an `events` row so the user can see what happened in History without re-running it.
- **Confirmation lives in the UI for risky actions.** Promote-to-LIVE, retire, kill-switch, mode-switch, force-close — all need an explicit click-to-confirm in the web UI. Operator-gated stays operator-gated regardless of surface.
- **Default to the UI in docs and READMEs.** Show the dashboard path first; show the CLI equivalent as a "for scripting" alternative.

When in doubt: if you wrote CLI-only, you wrote half the feature.

## Safety Invariants

**This system trades real money.** These are non-negotiable:

- **LLM never decides entries/exits/sizing.** LLM researches and recommends. Deterministic code executes.
- **Wash-sale and PDT pre-checks** before any order. No exceptions, no "we'll add it later."
- **Idempotent order submission.** Use `make_entry_order_id(strategy, candidate_id, ticker, side)` and `make_close_order_id(strategy, trade_id, ticker, side)`. Treat broker IDs as opaque; never parse or hand-build them.
- **Stale-quote kill switch.** Never submit when last quote > 10 seconds old.
- **Secrets never in repo, never in logs.** `.env` with 0600 permissions or OS keychain.
- **Operator-gated.** No code autonomously increases risk exposure. Agents recommend; operator decides.
- **Journal everything.** If a decision happens and the journal doesn't know about it, it's a bug.

## Tech Stack

Python 3.11+ · SQLite (WAL) · Flask + Jinja + HTMX · Ollama (Qwen 2.5 7B) · Claude API (Sonnet/Haiku) · Alpaca (IEX tier) · APScheduler · YAML config · Tailscale · Discord webhooks

Don't suggest alternative frameworks without good reason. This stack was chosen deliberately.

## Code Style

- **Brevity wins.** If there's a shorter way to write it that's equally clear, use it. No ceremony, no boilerplate for the sake of convention. This repo makes its own rules.
- **No fluff.** Skip defensive patterns nobody will hit, abstractions with one consumer, comments restating the code, and type annotations that add nothing.
- **Efficiency over elegance.** If a one-liner does the job, don't expand it into a design pattern.
- **But stay readable.** Brevity doesn't mean obfuscation. A human will read this and needs to understand it fast.

## Change Safety

**Every change is a potential break.** Before modifying anything:

1. Trace callers/importers — what depends on this?
2. Check for side effects — does this touch shared state, config, or external resources?
3. Does this cross a layer boundary? If so, check both sides.
4. Could this affect order submission, risk checks, or journal integrity? If yes, extra scrutiny.
5. Run existing tests if they exist. If they don't and the change is risky, say so.

Never assume a change is isolated. Always check.

## Thinking Like the User

- The human will use, read, and maintain this code. Optimize for their workflow.
- If something will be confusing in 2 weeks, add a short comment. Otherwise don't.
- Prefer patterns the user has already established in the codebase. Match their style, not a textbook.
- When offering choices, lead with what you'd actually recommend and why.

## Documentation

- **If you change code, check if docs need updating.** READMEs, inline docs, config examples, and `Project-HERON.md` if specs changed — scan them.
- **Don't create docs for docs' sake.** Only document what someone will actually look up.
- Keep docs next to the thing they describe when possible.

## Loop Trap Protocol

When Copilot gets stuck in a troubleshooting loop (trying the same fix repeatedly, oscillating between approaches, or not making progress after 2-3 attempts):

1. **Stop.** Flag it explicitly: "I'm looping on this."
2. **Step back.** Describe what was tried and why it failed.
3. **Resolve it** by trying a fundamentally different approach, asking the user, or breaking the problem down differently.
4. **Log it.** After resolution, add the pattern to `.github/instructions/known-pitfalls.instructions.md` so it never happens again.

## Git

- Commit messages: imperative mood, concise. `fix auth token refresh` not `Fixed the issue where the authentication token was not refreshing properly`.
- Don't amend published commits or force-push without asking.

## What NOT to Do (Unless asked)

- Don't add features that weren't asked for.
- Don't refactor working code that isn't being touched.
- Don't introduce dependencies without mentioning it.
- Don't over-engineer for hypothetical future requirements.
- Don't generate placeholder/example content unless asked.
- Don't put LLM logic in the execution hot path.
- Don't bypass risk checks for convenience.
- Don't hardcode API keys, tokens, or secrets anywhere.
