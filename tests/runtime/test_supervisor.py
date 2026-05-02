"""Tests for the Phase 3 supervisor / scheduler.

We avoid actually starting APScheduler in most tests — focus on the
journaling, command consumption, and run_once flow.
"""

import pytest

from heron.runtime.supervisor import Supervisor, request_command


def _ok_job(conn, mode):
    return {"status": "ok", "mode": mode}


def _bad_job(conn, mode):
    raise RuntimeError("boom")


def test_run_once_journals_success(conn):
    from apscheduler.triggers.interval import IntervalTrigger
    sup = Supervisor(mode="paper", conn=conn,
                     jobs=[("ok_job", _ok_job, IntervalTrigger(days=999), "ok")])
    try:
        result = sup.run_once("ok_job")
        assert result == {"status": "ok", "mode": "paper"}
        rows = conn.execute("SELECT * FROM scheduler_runs WHERE job_id='ok_job'").fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "ok"
        assert rows[0]["finished_at"] is not None
        assert rows[0]["error"] is None
    finally:
        sup.stop(wait=False)


def test_run_once_journals_failure(conn):
    from apscheduler.triggers.interval import IntervalTrigger
    sup = Supervisor(mode="paper", conn=conn,
                     jobs=[("bad_job", _bad_job, IntervalTrigger(days=999), "bad")])
    try:
        with pytest.raises(RuntimeError):
            sup.run_once("bad_job")
        row = conn.execute("SELECT * FROM scheduler_runs WHERE job_id='bad_job'").fetchone()
        assert row["status"] == "error"
        assert "boom" in row["error"]
    finally:
        sup.stop(wait=False)


def test_unknown_job_raises(conn):
    sup = Supervisor(mode="paper", conn=conn, jobs=[])
    try:
        with pytest.raises(KeyError):
            sup.run_once("missing")
    finally:
        sup.stop(wait=False)


def test_status_lists_jobs(conn):
    from apscheduler.triggers.interval import IntervalTrigger
    sup = Supervisor(mode="paper", conn=conn,
                     jobs=[("a", _ok_job, IntervalTrigger(days=1), "A"),
                           ("b", _ok_job, IntervalTrigger(days=2), "B")])
    try:
        s = sup.status()
        assert s["mode"] == "paper"
        assert {j["id"] for j in s["jobs"]} == {"a", "b"}
    finally:
        sup.stop(wait=False)


def test_invalid_mode_rejected(conn):
    with pytest.raises(ValueError):
        Supervisor(mode="dry-run", conn=conn, jobs=[])


def test_request_command_inserts_pending(conn):
    request_command(conn, "research_premarket", "run_now")
    row = conn.execute("SELECT * FROM scheduler_commands").fetchone()
    assert row["job_id"] == "research_premarket"
    assert row["action"] == "run_now"
    assert row["status"] == "pending"


def test_request_command_validates(conn):
    with pytest.raises(ValueError):
        request_command(conn, "x", "delete_everything")


def test_consume_pending_marks_consumed(conn):
    """The command-poller updates pending → consumed for known actions."""
    from apscheduler.triggers.interval import IntervalTrigger
    sup = Supervisor(mode="paper", conn=conn,
                     jobs=[("a", _ok_job, IntervalTrigger(days=999), "A")])
    sup.scheduler.start(paused=True)  # need scheduler started for pause/resume
    try:
        request_command(conn, "a", "pause")
        # Use a fresh connection like the real poller
        from heron.journal import get_journal_conn
        cmd_conn = get_journal_conn(str(conn.execute("PRAGMA database_list").fetchone()[2]))
        sup._consume_pending(cmd_conn)
        cmd_conn.close()
        row = conn.execute("SELECT * FROM scheduler_commands WHERE id=1").fetchone()
        assert row["status"] == "consumed"
    finally:
        sup.scheduler.shutdown(wait=False)


def test_consume_pending_records_error(conn):
    from apscheduler.triggers.interval import IntervalTrigger
    sup = Supervisor(mode="paper", conn=conn,
                     jobs=[("a", _ok_job, IntervalTrigger(days=999), "A")])
    try:
        request_command(conn, "missing_job", "pause")
        from heron.journal import get_journal_conn
        cmd_conn = get_journal_conn(str(conn.execute("PRAGMA database_list").fetchone()[2]))
        sup._consume_pending(cmd_conn)
        cmd_conn.close()
        row = conn.execute("SELECT * FROM scheduler_commands WHERE id=1").fetchone()
        assert row["status"] == "error"
        assert row["error"]
    finally:
        sup.stop(wait=False)


def test_default_jobs_registered():
    """Imported DEFAULT_JOBS contains the expected job ids."""
    from heron.runtime.supervisor import DEFAULT_JOBS
    ids = {j[0] for j in DEFAULT_JOBS}
    assert ids == {"research_premarket", "executor_cycle", "eod_debrief",
                   "daily_health", "heartbeat"}
