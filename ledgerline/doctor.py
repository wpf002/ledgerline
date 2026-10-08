"""
What's installed, what's missing, and what to run next.

Before 1.0 the only way to know whether this machine could run Ledgerline was
to have built it here. A second machine needed: the right Python, the venv, a
contact address the SEC accepts, a 5 GB cache, a database at the current
schema, the frozen Phase 0 record, and two background jobs that were installed
by hand. None of that was checked anywhere. Each check below names the command
that fixes it.

Read-only. Nothing here fetches, writes, or starts anything, except one HTTP
GET to the loopback read service to see whether it answers.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from . import edgar, schedule, status

OK, WARN, FAIL = "ok", "warn", "fail"

# launchd's status for a job it couldn't start: a working folder or log file
# it couldn't open, before the program ran at all.
EX_CONFIG = 78



def missed_weekday_scans(last: datetime, now: datetime) -> int:
    """Scheduled weekday runs (21:30 local) that should have happened after
    `last` and before `now`.

    Calendar days since the last scan undercount the problem: three days
    across a weekend is fine, three days mid-week is three lost scans. Counting
    the slots themselves is the number that matters.
    """
    from datetime import time, timedelta
    slot = time(schedule.SCAN_HOUR, schedule.SCAN_MINUTE)
    day = last.date() + timedelta(days=1)
    missed = 0
    while day <= now.date():
        due = datetime.combine(day, slot, tzinfo=now.tzinfo)
        if day.isoweekday() <= 5 and last < due <= now:
            missed += 1
        day += timedelta(days=1)
    return missed


@dataclass
class Check:
    state: str
    name: str
    detail: str
    fix: str | None = None


def _python() -> Check:
    v = sys.version_info
    if v >= (3, 11):
        return Check(OK, "Python", f"{v.major}.{v.minor}.{v.micro}")
    return Check(FAIL, "Python", f"{v.major}.{v.minor} is too old; 3.11 or newer is needed",
                 "install Python 3.12 and rerun ./bootstrap.sh")


def _package() -> Check:
    import ledgerline
    here = os.path.dirname(os.path.dirname(os.path.abspath(ledgerline.__file__)))
    if os.path.realpath(here) == os.path.realpath(schedule.REPO):
        return Check(OK, "Package", f"running from {here}")
    return Check(WARN, "Package",
                 f"imported from {here}, not from this repository",
                 "./bootstrap.sh (reinstalls the package from this checkout)")


def _contact() -> Check:
    ua = os.environ.get("LEDGERLINE_UA", "")
    if ua and "@" in ua:
        return Check(OK, "SEC contact", ua)
    return Check(FAIL, "SEC contact",
                 "LEDGERLINE_UA is missing or has no email address; the SEC "
                 "blocks automated readers that don't identify themselves",
                 'put LEDGERLINE_UA="Ledgerline research you@example.com" in .env')


def _cache() -> Check:
    facts = os.path.join(edgar.CACHE, "facts")
    if not os.path.isdir(facts):
        return Check(FAIL, "Filing cache", "empty: no company histories downloaded",
                     "ledgerline watch --add AAPL,MSFT && ledgerline fetch")
    n, size = 0, 0
    for entry in os.scandir(facts):
        if entry.is_file():
            n += 1
            size += entry.stat().st_size
    return Check(OK, "Filing cache", f"{n:,} company histories, {size / 1e9:.1f} GB")


def _database() -> Check:
    if not os.path.exists(edgar.DB_PATH):
        return Check(FAIL, "Database", "ledgerline/data/state.db doesn't exist",
                     "ledgerline watch --add AAPL,MSFT (creates it)")
    conn = sqlite3.connect(edgar.DB_PATH)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        n = conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        return Check(FAIL, "Database", f"can't be read: {exc}",
                     "move state.db aside and run ledgerline fetch to rebuild it")
    finally:
        conn.close()
    if version < edgar.SCHEMA_VERSION:
        return Check(WARN, "Database",
                     f"schema {version}, code expects {edgar.SCHEMA_VERSION}",
                     "any ledgerline command upgrades it on first use")
    if n == 0:
        return Check(WARN, "Database", "no companies on the watchlist",
                     "ledgerline watch --add AAPL,MSFT")
    return Check(OK, "Database", f"schema {version}, {n:,} companies watched")


def _frozen_record() -> Check:
    try:
        rec = status.load()
    except Exception as exc:  # noqa: BLE001 -- any failure here is the finding
        return Check(FAIL, "Phase 0 record",
                     f"can't be loaded ({exc}); every command that shows a "
                     "score refuses to run without it",
                     "git checkout ledgerline/data/phase0.json")
    return Check(OK, "Phase 0 record", f"verdict {rec['verdict']}, scored {rec['scored_on']}")


def _assessed() -> Check:
    if not os.path.exists(edgar.DB_PATH):
        return Check(WARN, "Assessability", "no database yet", "ledgerline check")
    conn = sqlite3.connect(edgar.DB_PATH)
    try:
        n = conn.execute("SELECT COUNT(DISTINCT cik) FROM scoreability").fetchone()[0]
    except sqlite3.OperationalError:
        n = 0
    finally:
        conn.close()
    if n == 0:
        return Check(WARN, "Assessability", "no company has been checked",
                     "ledgerline check")
    return Check(OK, "Assessability", f"{n:,} companies checked")


def _last_scan() -> Check:
    if not os.path.exists(edgar.DB_PATH):
        return Check(WARN, "Last scan", "never", "ledgerline scan --catch-up")
    conn = sqlite3.connect(edgar.DB_PATH)
    try:
        row = conn.execute("SELECT started_at FROM job_runs WHERE job = 'scan' "
                           "AND status = 'ok' ORDER BY started_at DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    if not row:
        return Check(WARN, "Last scan", "never", "ledgerline scan --catch-up")
    last = datetime.fromisoformat(row[0]).astimezone()
    missed = missed_weekday_scans(last, datetime.now().astimezone())
    when = last.strftime("%Y-%m-%d %H:%M")
    if missed:
        return Check(WARN, "Last scan",
                     f"{when}; {missed} scheduled weekday scan"
                     f"{'s' if missed != 1 else ''} missed since",
                     "ledgerline scan --catch-up, then ledgerline schedule install")
    return Check(OK, "Last scan", f"{when}, none missed since")


def _schedule() -> list[Check]:
    if not schedule.supported():
        return [Check(WARN, "Scheduled scan", "launchd isn't available on this system",
                      "add to crontab: " + schedule.cron_line())]
    out = []
    names = {schedule.SCAN_LABEL: "Scheduled scan", schedule.SERVICE_LABEL: "Read service job"}
    for s in schedule.status():
        name = names[s["label"]]
        if not s["installed"]:
            out.append(Check(WARN, name, "not installed", "ledgerline schedule install"))
        elif not s["current"]:
            out.append(Check(WARN, name, "installed, but points at another repo or Python",
                             "ledgerline schedule install"))
        elif not s["loaded"]:
            out.append(Check(WARN, name, "installed but not loaded",
                             "ledgerline schedule install"))
        elif s.get("last_exit") == EX_CONFIG:
            # launchd gave up before the program ran. From 2026-10-03 to
            # 2026-10-08 every scheduled scan ended this way because launchd
            # couldn't open reports/scan.log, a file cron had created. The
            # advice this check gave then, "scan --catch-up, then schedule
            # install", ran the scan by hand and left the job broken.
            log = os.path.relpath(s["log"]) if s.get("log") else "its log file"
            out.append(Check(WARN, name,
                             "launchd couldn't start it (exit 78): it couldn't "
                             f"open the working folder or {log}",
                             f"mv {log} {log}.old, then ledgerline schedule install"))
        elif s.get("last_exit"):
            out.append(Check(WARN, name, f"its last run exited with {s['last_exit']}",
                             f"read {os.path.relpath(s['log']) if s.get('log') else 'its log'}"))
        else:
            out.append(Check(OK, name, "installed and loaded"))
    if schedule.old_cron_present():
        out.append(Check(WARN, "Old cron entry",
                         "still in crontab; it double-schedules the scan and "
                         "skips days the machine was asleep",
                         "ledgerline schedule install (removes it)"))
    return out


def _service() -> Check:
    port = int(os.environ.get("PORT", "8787"))
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/validation", timeout=2) as r:
            if r.status == 200:
                return Check(OK, "Read service", f"answering on http://localhost:{port}")
    except OSError:
        pass
    return Check(WARN, "Read service", f"nothing answering on localhost:{port}",
                 "ledgerline publish && ledgerline schedule install")


def _feed() -> Check:
    from .api import views
    wl = os.path.join(views.FEED_DIR, "watchlist.json")
    if not os.path.exists(wl):
        return Check(WARN, "Published pages", "never published", "ledgerline publish")
    age = (datetime.now().timestamp() - os.path.getmtime(wl)) / 86400
    if age > 7:
        return Check(WARN, "Published pages", f"last published {age:.0f} days ago",
                     "ledgerline publish")
    return Check(OK, "Published pages", f"published {age:.0f} days ago")


def run() -> list[Check]:
    checks = [_python(), _package(), _contact(), _cache(), _database(),
              _frozen_record(), _assessed(), _last_scan()]
    checks += _schedule()
    checks += [_feed(), _service()]
    return checks
