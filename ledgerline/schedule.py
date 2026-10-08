"""
The two background jobs, installed from code instead of by hand.

Until 1.0 both were hand-installed on one machine: a cron line for the daily
scan and a LaunchAgent plist for the read service, neither written down
anywhere a second machine could find. cron also skipped every run where the
Mac was asleep at 21:30 and never ran it later -- September 2026 lost about
half its weekdays to that.

Both jobs are launchd LaunchAgents now:

  com.ledgerline.scan     weekdays 21:30 local, `scan --catch-up`. launchd
                          runs a missed StartCalendarInterval job when the
                          machine wakes, and --catch-up reads every daily list
                          since the last completed scan, so a long sleep can't
                          drop days either.
  com.ledgerline.service  the loopback read service, kept alive.

macOS only. On anything else `install` refuses and prints the equivalent cron
line instead of guessing at a systemd setup nobody has tested.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.expanduser("~/Library/LaunchAgents")
REPORTS = os.path.join(REPO, "reports")

SCAN_LABEL = "com.ledgerline.scan"
SERVICE_LABEL = "com.ledgerline.service"

SCAN_HOUR, SCAN_MINUTE = 21, 30
WEEKDAYS = (1, 2, 3, 4, 5)  # launchd: 0 and 7 are Sunday


@dataclass
class Job:
    label: str
    plist: dict

    @property
    def path(self) -> str:
        return os.path.join(AGENTS, f"{self.label}.plist")


def _python() -> str:
    """The interpreter running this command, which is the venv's when the CLI
    was installed by bootstrap.sh. Recorded by absolute path because launchd
    starts jobs with a minimal environment and no PATH worth relying on."""
    return sys.executable


def scan_job() -> Job:
    return Job(SCAN_LABEL, {
        "Label": SCAN_LABEL,
        "ProgramArguments": [_python(), "-m", "ledgerline.cli", "scan", "--catch-up"],
        "WorkingDirectory": REPO,
        "StartCalendarInterval": [
            {"Weekday": d, "Hour": SCAN_HOUR, "Minute": SCAN_MINUTE} for d in WEEKDAYS
        ],
        "StandardOutPath": os.path.join(REPORTS, "scan.log"),
        "StandardErrorPath": os.path.join(REPORTS, "scan.log"),
        # Not RunAtLoad: installing should not fire a scan the user didn't ask for.
        "RunAtLoad": False,
    })


def service_job() -> Job:
    node = shutil.which("node") or "/usr/local/bin/node"
    return Job(SERVICE_LABEL, {
        "Label": SERVICE_LABEL,
        "ProgramArguments": [node, os.path.join(REPO, "service", "server.mjs")],
        "WorkingDirectory": REPO,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": os.path.join(REPORTS, "service.log"),
        "StandardErrorPath": os.path.join(REPORTS, "service.log"),
    })


def jobs() -> list[Job]:
    return [scan_job(), service_job()]


def cron_line() -> str:
    """What to put in crontab on a machine without launchd. Same command; it
    inherits cron's no-catch-up-on-sleep behaviour, which --catch-up then
    covers on the next run that does fire."""
    return (f"{SCAN_MINUTE} {SCAN_HOUR} * * 1-5 cd {REPO} && {_python()} "
            f"-m ledgerline.cli scan --catch-up >> {REPORTS}/scan.log 2>&1")


def supported() -> bool:
    return sys.platform == "darwin"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _old_cron_entries() -> list[str]:
    """The hand-installed cron line from before 1.0. Left in place it would
    double-schedule the scan."""
    out = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [ln for ln in out.stdout.splitlines() if "ledgerline.cli scan" in ln]


def _remove_old_cron() -> int:
    out = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if out.returncode != 0:
        return 0
    keep = [ln for ln in out.stdout.splitlines() if "ledgerline.cli scan" not in ln]
    removed = len(out.stdout.splitlines()) - len(keep)
    if removed:
        subprocess.run(["crontab", "-"], input="\n".join(keep) + ("\n" if keep else ""),
                       text=True, check=True)
    return removed


def install() -> dict:
    """Write both plists and (re)load them. Idempotent: reinstalling replaces
    the files and reloads, which is also how a moved repo or a new venv gets
    picked up."""
    if not supported():
        raise RuntimeError(
            "Scheduling is built for macOS launchd. On this system add this "
            "line with `crontab -e` instead:\n  " + cron_line()
        )
    os.makedirs(AGENTS, exist_ok=True)
    os.makedirs(REPORTS, exist_ok=True)
    done = []
    for job in jobs():
        _launchctl("unload", job.path)
        with open(job.path, "wb") as fh:
            plistlib.dump(job.plist, fh)
        res = _launchctl("load", job.path)
        done.append({"label": job.label, "path": job.path,
                     "loaded": res.returncode == 0, "error": res.stderr.strip() or None})
    return {"jobs": done, "cron_removed": _remove_old_cron()}


def uninstall() -> dict:
    if not supported():
        raise RuntimeError("Nothing to uninstall: scheduling is macOS launchd only.")
    removed = []
    for job in jobs():
        if os.path.exists(job.path):
            _launchctl("unload", job.path)
            os.remove(job.path)
            removed.append(job.label)
    return {"removed": removed}


def last_exit(listed: str, label: str) -> int | None:
    """The exit status `launchctl list` reports for one job, or None when the
    job isn't listed or hasn't exited. The columns are PID, status, label."""
    for line in listed.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2] == label:
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def status() -> list[dict]:
    """Per job: is the plist installed, does launchd know about it, does the
    installed file still point at THIS repo and THIS interpreter, and how did
    its last start end."""
    out = []
    listed = _launchctl("list").stdout if supported() else ""
    for job in jobs():
        installed = os.path.exists(job.path)
        current = False
        if installed:
            try:
                with open(job.path, "rb") as fh:
                    current = plistlib.load(fh).get("ProgramArguments") == \
                        job.plist["ProgramArguments"]
            except (OSError, plistlib.InvalidFileException):
                current = False
        out.append({"label": job.label, "installed": installed,
                    "loaded": job.label in listed, "current": current,
                    "last_exit": last_exit(listed, job.label),
                    "log": job.plist.get("StandardOutPath")})
    return out


def old_cron_present() -> bool:
    return bool(_old_cron_entries())
