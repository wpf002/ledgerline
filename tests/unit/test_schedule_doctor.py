"""Scheduling and the doctor command (ROADMAP_V1 1.0-d).

Each test pins a defect the old hand-installed setup had, or a promise the new
one makes.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from ledgerline import doctor, edgar, ingest, schedule, status

ET = timezone(timedelta(hours=-4))


# ------------------------------------------------------------- catch-up days


@pytest.fixture
def scans_db(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr(edgar, "DB_PATH", str(path))
    conn = edgar.db()
    conn.close()

    def add(started_at: str, status_: str = "ok"):
        c = sqlite3.connect(path)
        with c:
            c.execute("INSERT INTO job_runs (job, status, started_at) VALUES (?,?,?)",
                      ("scan", status_, started_at))
        c.close()

    return add


def test_catch_up_with_no_history_reads_two_days(scans_db):
    assert ingest.catch_up_days(date(2026, 10, 3)) == 2


def test_catch_up_reads_every_day_since_the_last_completed_scan(scans_db):
    """cron read a fixed --days-back 2, so after a sleep the days in between
    were never read. September 2026 lost about half its weekdays that way."""
    scans_db("2026-09-30T02:30:01+00:00")
    assert ingest.catch_up_days(date(2026, 10, 3)) == 5


def test_catch_up_ignores_failed_runs(scans_db):
    """A run that crashed didn't read its days. Counting from it would skip them."""
    scans_db("2026-09-20T02:30:00+00:00")
    scans_db("2026-09-30T02:30:00+00:00", status_="failed")
    assert ingest.catch_up_days(date(2026, 10, 3)) == 15


def test_catch_up_is_capped(scans_db):
    scans_db("2025-01-01T02:30:00+00:00")
    assert ingest.catch_up_days(date(2026, 10, 3)) == ingest.CATCH_UP_CAP_DAYS


# ---------------------------------------------------- missed weekday slots


def test_missed_scans_counts_weekday_slots_not_calendar_days():
    """Three calendar days over a weekend lose nothing; three mid-week lose
    three scans. The old 'days since' check called both fine."""
    last = datetime(2026, 9, 29, 21, 30, tzinfo=ET)    # Tue
    now = datetime(2026, 10, 3, 12, 0, tzinfo=ET)      # Sat
    assert doctor.missed_weekday_scans(last, now) == 3  # Wed Thu Fri


def test_weekend_gap_misses_nothing():
    last = datetime(2026, 10, 2, 21, 30, tzinfo=ET)    # Fri
    now = datetime(2026, 10, 5, 9, 0, tzinfo=ET)       # Mon morning
    assert doctor.missed_weekday_scans(last, now) == 0


def test_todays_slot_counts_only_after_it_passes():
    last = datetime(2026, 10, 2, 21, 30, tzinfo=ET)    # Fri
    assert doctor.missed_weekday_scans(last, datetime(2026, 10, 5, 21, 29, tzinfo=ET)) == 0
    assert doctor.missed_weekday_scans(last, datetime(2026, 10, 5, 21, 31, tzinfo=ET)) == 1


# --------------------------------------------------------------- the jobs


def test_scan_job_runs_weekdays_with_catch_up():
    plist = schedule.scan_job().plist
    slots = plist["StartCalendarInterval"]
    assert sorted(s["Weekday"] for s in slots) == [1, 2, 3, 4, 5]
    assert all((s["Hour"], s["Minute"]) == (21, 30) for s in slots)
    assert "--catch-up" in plist["ProgramArguments"]


def test_installing_does_not_fire_a_scan():
    assert schedule.scan_job().plist["RunAtLoad"] is False


def test_service_job_is_kept_alive():
    plist = schedule.service_job().plist
    assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True


def test_jobs_use_absolute_paths():
    """launchd starts jobs with a minimal environment. A bare `python` or a
    relative path works in a terminal and fails silently at 21:30."""
    for job in schedule.jobs():
        assert job.plist["ProgramArguments"][0].startswith("/")
        assert job.plist["WorkingDirectory"].startswith("/")


def test_install_refuses_off_macos_and_gives_the_cron_line(monkeypatch):
    monkeypatch.setattr(schedule.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="crontab"):
        schedule.install()
    assert "--catch-up" in schedule.cron_line()


# -------------------------------------------------------------- the doctor


def test_doctor_fails_without_a_contact_address(monkeypatch):
    monkeypatch.delenv("LEDGERLINE_UA", raising=False)
    assert doctor._contact().state == doctor.FAIL


def test_doctor_fails_without_the_frozen_record(monkeypatch):
    """Every score-showing command refuses without phase0.json. The doctor is
    where that should surface first, not mid-scan."""
    def boom():
        raise RuntimeError("missing")
    monkeypatch.setattr(status, "load", boom)
    check = doctor._frozen_record()
    assert check.state == doctor.FAIL and check.fix


def test_every_non_ok_check_says_what_to_run(monkeypatch):
    monkeypatch.delenv("LEDGERLINE_UA", raising=False)
    for c in doctor.run():
        if c.state != doctor.OK:
            assert c.fix, f"{c.name} is {c.state} with no fix"
