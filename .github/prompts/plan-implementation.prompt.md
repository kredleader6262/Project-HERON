---
description: "Plan the next implementation milestone for Project HERON. Reviews current state, uses Project-HERON-v4.md Section 15 stage boundaries, and produces an actionable task breakdown."
agent: "agent"
tools: ["search", "codebase"]
---
# Plan Implementation

You are planning the next implementation milestone for Project HERON, an LLM-augmented algorithmic trading system.

## Context

- Read [Project-HERON-v4.md](../../Project-HERON-v4.md) Section 15 for current stage boundaries and [Project-HERON-v3.md](../../Project-HERON-v3.md) Section 15 for the historical 16-milestone roadmap.
- Scan the current codebase to determine what's already built.
- Read [copilot-instructions.md](../copilot-instructions.md) for coding rules.

## Task

1. **Assess current state.** What milestones are complete? What exists in the repo?
2. **Identify the next milestone.** Pick the lowest-numbered incomplete milestone. Explain why it's next.
3. **Break it into tasks.** Concrete, ordered, with file paths where code should go. Each task should be completable in one focused chat session.
4. **Flag risks.** What could go wrong? What needs operator decisions before coding starts?
5. **Propose the directory/module structure** for this milestone, consistent with the 5-layer architecture.

Keep it actionable. No fluff.
