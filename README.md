# Project HERON

*Hypothesis-driven Execution with Research, Observation, and Notation*

An LLM-augmented algorithmic trading system on a $500 seed account. The LLM researches; deterministic code executes. The journal is the product.

## What This Is

A learning-first system that uses local and API language models to surface trading candidates, then validates and executes them through strict rule-based code with hard risk limits. Every decision is journaled. Every strategy runs against a deterministic baseline before touching real money.

**Full spec:** [`Project-HERON.md`](Project-HERON.md)

## Stack

Python 3.11+ · SQLite (WAL) · Flask + HTMX · Ollama (Qwen 2.5 7B) · Claude API · Alpaca (IEX tier) · APScheduler · YAML config · Tailscale · Discord webhooks

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

See [`Project-HERON.md` Section 18](Project-HERON.md) for bootstrap instructions.

```bash
cp .env.example .env   # fill in API keys
ollama pull qwen2.5:7b-instruct-q4_K_M
```

## Status

Pre-implementation. Spec complete, roadmap in [`Project-HERON.md` Section 15](Project-HERON.md).
