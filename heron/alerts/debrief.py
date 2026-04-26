"""End-of-day debrief (M12).

Aggregates today's trades, costs, and events; asks Claude to write a short
plain-text summary; posts to Discord as the 'debrief' category.
"""

import json
import logging
from datetime import datetime, timezone, timedelta

from heron.config import CLAUDE_SONNET_MODEL, MONTHLY_COST_CEILING
from heron.journal.ops import get_daily_costs, get_monthly_cost, get_events
from heron.journal.trades import list_trades
from heron.research.claude import call
from heron.research.progress import Spinner
from heron.alerts.discord import send as discord_send, dashboard_link
from heron.util import trading_day_ny, trading_day_of_iso

log = logging.getLogger(__name__)

_DEBRIEF_PROMPT = """You are writing an end-of-day debrief for a systematic trading operator.

Data for {date}:
- Closed trades today: {closed_count} ({winners} winners, {losers} losers)
- Realized P&L: ${pnl:.2f}
- Open positions carrying overnight: {open_count}
- Claude API cost today: ${cost_today:.2f} (month-to-date ${cost_mtd:.2f} of ${ceiling:.2f})
- Notable events: {events}

Trade details (JSON):
{trades_json}

Write a concise 3–5 sentence plain-text summary for the operator. Be direct,
no fluff. Mention the biggest winner/loser by name if any. Flag risks.
Return JSON: {{"summary": "...", "flag_for_attention": true/false}}"""


def _today_utc():
    # Retained name for API compat; returns the NY trading day so the debrief
    # captures trades closed late in the session (e.g., 21:00 ET) that would
    # otherwise fall into tomorrow under pure UTC.
    return trading_day_ny()


def _is_today(iso_ts, today):
    return bool(iso_ts) and trading_day_of_iso(iso_ts) == today


def gather(conn, date=None):
    """Collect today's state from the journal. Returns a dict."""
    date = date or _today_utc()

    all_trades = list_trades(conn)
    closed_today = []
    open_overnight = 0
    for t in all_trades:
        if t["close_filled_at"] and _is_today(t["close_filled_at"], date):
            closed_today.append(t)
        elif not t["close_price"] and t["fill_price"]:
            open_overnight += 1

    pnl = sum((t["pnl"] or 0) for t in closed_today)
    winners = sum(1 for t in closed_today if (t["pnl"] or 0) > 0)
    losers = sum(1 for t in closed_today if (t["pnl"] or 0) < 0)

    # Largest winner/loser
    best = max(closed_today, key=lambda t: t["pnl"] or 0, default=None)
    worst = min(closed_today, key=lambda t: t["pnl"] or 0, default=None)

    cost_today = sum(r["cost"] for r in get_daily_costs(conn, date))
    cost_mtd = get_monthly_cost(conn)

    events = get_events(conn, limit=10)
    event_msgs = [e["message"] for e in events
                  if _is_today(e["created_at"], date)]

    return {
        "date": date,
        "closed_count": len(closed_today),
        "winners": winners,
        "losers": losers,
        "pnl": pnl,
        "open_count": open_overnight,
        "cost_today": cost_today,
        "cost_mtd": cost_mtd,
        "best": dict(best) if best else None,
        "worst": dict(worst) if worst else None,
        "closed_trades": [
            {"ticker": t["ticker"], "side": t["side"],
             "pnl": t["pnl"], "pnl_pct": t["pnl_pct"],
             "close_reason": t["close_reason"]}
            for t in closed_today
        ],
        "events": event_msgs,
    }


def write_prose(summary_data):
    """Ask Claude for a short prose debrief. Returns dict with text + cost."""
    prompt = _DEBRIEF_PROMPT.format(
        date=summary_data["date"],
        closed_count=summary_data["closed_count"],
        winners=summary_data["winners"],
        losers=summary_data["losers"],
        pnl=summary_data["pnl"],
        open_count=summary_data["open_count"],
        cost_today=summary_data["cost_today"],
        cost_mtd=summary_data["cost_mtd"],
        ceiling=MONTHLY_COST_CEILING,
        events=", ".join(summary_data["events"][:5]) or "none",
        trades_json=json.dumps(summary_data["closed_trades"])[:1500],
    )
    with Spinner("Claude debrief prose"):
        result = call(prompt, model=CLAUDE_SONNET_MODEL, json_mode=True,
                      max_tokens=512, temperature=0.4)
    parsed = result.get("parsed") or {}
    return {
        "summary": parsed.get("summary", "").strip(),
        "flag": bool(parsed.get("flag_for_attention")),
        "cost_usd": result.get("cost_usd", 0.0),
        "tokens_in": result.get("tokens_in", 0),
        "tokens_out": result.get("tokens_out", 0),
    }


def _format_message(data, prose):
    lines = [
        f"**EOD Debrief — {data['date']}**",
        (f"Closed {data['closed_count']} trades · "
         f"{data['winners']}W / {data['losers']}L · "
         f"P&L ${data['pnl']:+.2f}"),
        f"Open overnight: {data['open_count']} · "
        f"Cost MTD: ${data['cost_mtd']:.2f}/${MONTHLY_COST_CEILING:.0f}",
    ]
    if prose and prose.get("summary"):
        lines.append("")
        lines.append(prose["summary"])
    if prose and prose.get("flag"):
        lines.append("⚠ Flagged for attention.")
    lines.append(f"\n{dashboard_link('/')}")
    return "\n".join(lines)


def run(conn, *, deliver=True, dry_run=False):
    """Execute EOD debrief: gather → prose → deliver.

    deliver=False skips Discord send (for tests / CLI preview).
    dry_run=True still calls Claude but doesn't post to Discord.
    """
    data = gather(conn)
    prose = {"summary": "", "flag": False, "cost_usd": 0.0}

    # Skip Claude call on empty days — save cost
    if data["closed_count"] > 0 or data["events"]:
        try:
            prose = write_prose(data)
        except Exception as e:
            log.warning(f"Debrief prose failed: {e}")
            prose = {"summary": f"(prose unavailable: {e})",
                     "flag": False, "cost_usd": 0.0}

    message = _format_message(data, prose)

    delivery = None
    if deliver:
        delivery = discord_send("debrief", message, dry_run=dry_run)

    return {
        "data": data,
        "prose": prose,
        "message": message,
        "delivery": delivery,
    }
