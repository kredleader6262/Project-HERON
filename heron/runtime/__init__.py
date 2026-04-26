"""Runtime supervisor (Phase 3).

`heron run` enters the supervisor: an APScheduler-backed daemon that fires
research / executor / debrief / health jobs on a market-aware schedule, writes
every run to the journal, and shuts down gracefully on SIGINT/SIGTERM.

The schedule is defined in code (`heron.runtime.supervisor.DEFAULT_JOBS`).
Run history lives in `scheduler_runs`; operator pokes (run-now / pause / resume)
go through `scheduler_commands` so the dashboard process never needs IPC.
"""
