"""Supervisor — APScheduler-backed daemon that fires HERON's recurring jobs.

Persistence:
  - Schedule lives in code (DEFAULT_JOBS) — re-registered on every start.
  - Run history is journaled to `scheduler_runs`.
  - Operator pokes (run-now / pause / resume) come through `scheduler_commands`,
    polled every `_COMMAND_POLL_S` seconds. Avoids the need for IPC between
    the dashboard process and this one.

Mode discipline:
  - Supervisor is initialized with mode='paper' or 'live'. The mode is passed
    into every job and into `pre_trade_checks` via the executor cycle.
  - Live mode requires the preflight check to pass with no blockers.
"""

import logging
import threading
import time
from datetime import datetime, timezone

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pytz import timezone as pytz_timezone

from heron.journal import get_journal_conn, init_journal
from heron.runtime.jobs import (
    job_research_premarket, job_executor_cycle,
    job_eod_debrief, job_daily_health, job_heartbeat,
)
from heron.util import utc_now_iso

log = logging.getLogger(__name__)

NY = pytz_timezone("America/New_York")

# (job_id, callable, trigger, description)
DEFAULT_JOBS = [
    ("research_premarket", job_research_premarket,
     CronTrigger(hour=6, minute=30, day_of_week="mon-fri", timezone=NY),
     "Pre-market research (06:30 ET, mon-fri)"),
    ("executor_cycle", job_executor_cycle,
     CronTrigger(minute="*/5", hour="9-15", day_of_week="mon-fri", timezone=NY),
     "Executor cycle (every 5min during session)"),
    ("eod_debrief", job_eod_debrief,
     CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone=NY),
     "EOD debrief (16:30 ET, mon-fri)"),
    ("daily_health", job_daily_health,
     CronTrigger(hour=8, minute=0, timezone=NY),
     "Daily health check (08:00 ET)"),
    ("heartbeat", job_heartbeat,
     IntervalTrigger(minutes=60),
     "Hourly heartbeat"),
]

_COMMAND_POLL_S = 10


class Supervisor:
    """Owns the BackgroundScheduler and the command-polling thread."""

    def __init__(self, mode="paper", conn=None, jobs=None):
        if mode not in ("paper", "live"):
            raise ValueError(f"mode must be 'paper' or 'live', got {mode!r}")
        self.mode = mode
        self._own_conn = conn is None
        self.conn = conn or get_journal_conn()
        if self._own_conn:
            init_journal(self.conn)

        self.scheduler = BackgroundScheduler(timezone=NY)
        self.scheduler.add_listener(self._on_job_event,
                                    EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
        # Local registry mirrors registered jobs so run_once/status work
        # without requiring the scheduler to be started.
        self._jobs = {}  # job_id → (callable, trigger, description)
        self._cmd_stop = threading.Event()
        self._cmd_thread = None

        for job_id, fn, trigger, desc in (jobs if jobs is not None else DEFAULT_JOBS):
            self._register(job_id, fn, trigger, desc)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self):
        log.info(f"Supervisor starting (mode={self.mode})")
        self.scheduler.start()
        self._cmd_stop.clear()
        self._cmd_thread = threading.Thread(
            target=self._poll_commands, daemon=True, name="heron-cmd-poller"
        )
        self._cmd_thread.start()

    def stop(self, *, wait=True):
        log.info("Supervisor stopping")
        self._cmd_stop.set()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=wait)
        if self._cmd_thread and self._cmd_thread.is_alive():
            self._cmd_thread.join(timeout=5)
        if self._own_conn:
            self.conn.close()

    # ── Public API ─────────────────────────────────────────────────────────

    def run_once(self, job_id):
        """Fire a job synchronously, outside the scheduler. Used by --once."""
        if job_id not in self._jobs:
            raise KeyError(f"unknown job: {job_id!r}")
        fn, _trig, _desc = self._jobs[job_id]
        return self._invoke(job_id, fn)

    def status(self):
        """Return summary of jobs + recent runs for dashboards/CLI."""
        jobs = []
        for jid, (_fn, _trig, desc) in self._jobs.items():
            scheduled = self.scheduler.get_job(jid) if self.scheduler.running else None
            next_run = getattr(scheduled, "next_run_time", None) if scheduled else None
            jobs.append({
                "id": jid,
                "name": desc,
                "next_run": next_run.isoformat() if next_run else None,
            })
        recent = [dict(r) for r in self.conn.execute(
            "SELECT * FROM scheduler_runs ORDER BY started_at DESC LIMIT 20"
        ).fetchall()]
        return {"mode": self.mode, "jobs": jobs, "recent_runs": recent}

    # ── Internal ───────────────────────────────────────────────────────────

    def _register(self, job_id, fn, trigger, desc):
        self._jobs[job_id] = (fn, trigger, desc)
        self.scheduler.add_job(
            self._wrap(job_id, fn), trigger=trigger,
            id=job_id, name=desc, replace_existing=True,
            max_instances=1, coalesce=True,
        )

    def _wrap(self, job_id, fn):
        """Wrap a job so we can record a `scheduler_runs` row before it runs."""
        def _runner():
            run_id = self._begin_run(job_id)
            try:
                result = fn(self.conn, self.mode)
                self._finish_run(run_id, "ok", result)
            except Exception as e:
                self._finish_run(run_id, "error", None, error=str(e))
                raise
        return _runner

    def _invoke(self, job_id, fn):
        """Run a job synchronously and journal it. Returns the result dict."""
        run_id = self._begin_run(job_id)
        try:
            result = fn(self.conn, self.mode)
            self._finish_run(run_id, "ok", result)
            return result
        except Exception as e:
            self._finish_run(run_id, "error", None, error=str(e))
            raise

    def _begin_run(self, job_id):
        cur = self.conn.execute(
            """INSERT INTO scheduler_runs (job_id, started_at, status)
               VALUES (?, ?, 'running')""",
            (job_id, utc_now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def _finish_run(self, run_id, status, result, error=None):
        import json as _json
        try:
            summary = _json.dumps(result)[:2000] if result else None
        except (TypeError, ValueError):
            summary = str(result)[:2000]
        self.conn.execute(
            """UPDATE scheduler_runs
               SET finished_at=?, status=?, result_summary=?, error=?
               WHERE id=?""",
            (utc_now_iso(), status, summary, error, run_id),
        )
        self.conn.commit()

    def _on_job_event(self, event):
        """APS event listener — secondary safety net; primary recording is in _wrap."""
        if event.exception:
            log.error(f"job {event.job_id} failed: {event.exception}")

    # ── Command polling ────────────────────────────────────────────────────

    def _poll_commands(self):
        """Poll `scheduler_commands` for operator pokes from the dashboard."""
        # Use a private connection so we don't race with job runs on `self.conn`.
        cmd_conn = get_journal_conn()
        try:
            while not self._cmd_stop.is_set():
                try:
                    self._consume_pending(cmd_conn)
                except Exception as e:
                    log.warning(f"command poll error: {e}")
                self._cmd_stop.wait(_COMMAND_POLL_S)
        finally:
            cmd_conn.close()

    def _consume_pending(self, cmd_conn):
        rows = cmd_conn.execute(
            "SELECT * FROM scheduler_commands WHERE status='pending' ORDER BY id"
        ).fetchall()
        for row in rows:
            err = None
            try:
                self._apply_command(row["job_id"], row["action"])
            except Exception as e:
                err = str(e)
                log.warning(f"command {row['id']} ({row['action']} {row['job_id']}) failed: {e}")
            cmd_conn.execute(
                """UPDATE scheduler_commands
                   SET status=?, consumed_at=?, error=?
                   WHERE id=?""",
                ("error" if err else "consumed", utc_now_iso(), err, row["id"]),
            )
            cmd_conn.commit()

    def _apply_command(self, job_id, action):
        if action == "run_now":
            j = self.scheduler.get_job(job_id)
            if not j:
                raise KeyError(f"unknown job: {job_id}")
            j.modify(next_run_time=datetime.now(timezone.utc))
        elif action == "pause":
            self.scheduler.pause_job(job_id)
        elif action == "resume":
            self.scheduler.resume_job(job_id)
        else:
            raise ValueError(f"unknown action: {action!r}")


def request_command(conn, job_id, action):
    """Dashboard helper: queue an operator command for the supervisor."""
    if action not in ("run_now", "pause", "resume"):
        raise ValueError(f"invalid action: {action!r}")
    conn.execute(
        """INSERT INTO scheduler_commands (job_id, action, requested_at)
           VALUES (?, ?, ?)""",
        (job_id, action, utc_now_iso()),
    )
    conn.commit()
