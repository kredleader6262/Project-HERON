---
description: "Plan the next implementation milestone for Project HERON. Reviews current state, picks the next milestone from Project-HERON.md Section 15, and produces an actionable task breakdown."
agent: "agent"
tools: ["search", "codebase"]
---
# Plan Implementation

You are planning the next implementation milestone for Project HERON, an LLM-augmented algorithmic trading system.

## Context

- Read [Project-HERON.md](../Project-HERON.md) Section 15 for the full roadmap (16 milestones, each independently demo-able).
- Scan the current codebase to determine what's already built.
- Read [copilot-instructions.md](../copilot-instructions.md) for coding rules.

## Task

1. **Assess current state.** What milestones are complete? What exists in the repo?
2. **Identify the next milestone.** Pick the lowest-numbered incomplete milestone. Explain why it's next.
3. **Break it into tasks.** Concrete, ordered, with file paths where code should go. Each task should be completable in one focused chat session.
4. **Flag risks.** What could go wrong? What needs operator decisions before coding starts?
5. **Propose the directory/module structure** for this milestone, consistent with the 5-layer architecture.

Keep it actionable. No fluff.
