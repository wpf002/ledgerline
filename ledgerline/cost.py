"""
The run-cost curve, measured rather than asserted.

ROADMAP §10 claims "cost per run should stay flat in universe size". Measured,
that claim is half true, and this module reports which half instead of
averaging the disagreement away:

  * Tier 0 IS flat: one daily-index request covers the whole market, ~0.22s
    including parse, independent of how many companies are watched. True.
  * The RUN is not: per-run cost is 1 + K(N) requests, where K is how many
    watched companies actually filed that day, and K is linear in N. Over 504
    business days of 2024-2025 at N=1,498, periodic forms arrived at median
    7/day, p90 97/day, max 201/day, with companyfacts payloads at 3.40 MB
    mean. Disk and Tier 1 bytes are the binding constraint, not requests.

Everything here is a REPLAY: the real historical arrival series comes from
fullindex.arrivals() (sqlite only), and the per-filer constants are measured
from whatever companyfacts documents are already on disk. Zero network -- a
cost model that quietly hits the network is not a model and cannot run in CI.
Live byte counting belongs to the STATS meter in edgar.fetch(), which every
run already books into job_runs; there is deliberately no "live cost" command,
because a deliberate 400-request day against SEC to confirm arithmetic is
itself the cost being avoided.

Costs are reported as median / p90 / max per day, never as a mean: filing
arrival is violently clustered around filing season (median 7/day against max
201/day), so a mean describes no day that ever happens and understates the
peak ~29x. And the replay model names its own bias the way scripts/sp1500.py
does: its constants come from the local cache, which is S&P 1500 and large-cap
skewed, so byte projections OVERSTATE cost at scale -- smaller filers file
smaller documents.

Every payload is stamped with the frozen Phase 0 verdict via status.stamp().
A cost curve for scaling a detector is only honest next to the fact that the
detector failed its own test.
"""
from __future__ import annotations

import json
import os
import random
import time
from datetime import date, timedelta

from . import edgar, fullindex, status

DEFAULT_SIZES = (100, 500, 1500, 3000)

# Named in the payload, not left for someone to discover.
BIAS_NOTE = (
    "Per-company document sizes are measured from the companies already "
    "downloaded, which skews toward large filers; smaller companies file "
    "smaller documents, so byte projections here OVERSTATE cost at scale."
)


def _summary(values: list[float]) -> dict:
    """median / p90 / max, plain Python. Never a mean -- see module docstring."""
    if not values:
        return {"median": None, "p90": None, "max": None}
    ordered = sorted(values)
    n = len(ordered)
    median = (ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2)
    p90 = ordered[min(n - 1, max(0, -(-9 * n // 10) - 1))]
    return {"median": median, "p90": p90, "max": ordered[-1]}


def measure_constants(sample_n: int = 40, seed: int = 7) -> dict:
    """Per-filer byte and parse-time constants from the local facts cache.

    Reads only files already on disk. When the cache is empty every value is
    None, never a guess -- replay() then refuses to project, because a cost
    number invented from nothing would be read as a measurement.
    """
    facts_dir = os.path.join(edgar.CACHE, "facts")
    names = sorted(os.listdir(facts_dir)) if os.path.isdir(facts_dir) else []
    names = [n for n in names if n.endswith(".json")]
    if not names:
        return {"n_sampled": 0, "bytes": None, "parse_seconds": None, "bias": BIAS_NOTE}
    picks = random.Random(seed).sample(names, min(sample_n, len(names)))
    sizes, secs = [], []
    for name in picks:
        path = os.path.join(facts_dir, name)
        sizes.append(float(os.path.getsize(path)))
        with open(path, "rb") as fh:
            body = fh.read()
        t0 = time.monotonic()
        try:
            json.loads(body)
        except ValueError:
            continue  # a corrupt cache file measures nothing
        secs.append(time.monotonic() - t0)
    return {
        "n_sampled": len(picks),
        "bytes": _summary(sizes),
        "parse_seconds": _summary(secs),
        "bias": BIAS_NOTE,
    }


def _business_days(start: str, end: str) -> list[str]:
    d, stop = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while d <= stop:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def replay(ciks: set[str], start: str, end: str, constants: dict) -> dict:
    """One universe size replayed against the real filing calendar. No network.

    Quiet days count: a day nobody filed is still a run (one Tier 0 request,
    zero refreshes), and leaving those days out would inflate every median.
    """
    if not constants.get("bytes") or constants["bytes"].get("median") is None:
        raise ValueError(
            "No downloaded filing data to measure per-company sizes from, so "
            "there is nothing honest to project. Run `ledgerline fetch` first."
        )
    arr = fullindex.arrivals(ciks, start, end)
    days = _business_days(start, end)
    per_day = [float(arr.get(day, 0)) for day in days]
    b_med = constants["bytes"]["median"]
    parse = (constants.get("parse_seconds") or {}).get("median")
    return {
        "n_filers": len(ciks),
        "days": len(days),
        # The half of the ROADMAP claim that is true: the market-wide change
        # detector is one request per run at every N.
        "tier0_requests_per_run": 1.0,
        # The half that is false: refreshes track the day's filers.
        "tier1_requests_per_day": _summary(per_day),
        "bytes_per_day": _summary([k * b_med for k in per_day]),
        "seconds_per_day": (_summary([k * parse for k in per_day])
                            if parse is not None else None),
        "disk_bytes_projected": int(len(ciks) * b_med),
    }


def measure_scaling(sizes: list[int], start: str, end: str, seed: int = 7,
                    constants: dict | None = None) -> dict:
    """The scaling curve: replay at each size, tiers reported separately.

    Samples are deterministic (seeded per size) draws from the registry --
    there is no free market-cap series, so "the N largest" is not
    constructible and a random labelled sample is the honest stand-in. The
    tier0 and tier1 columns stay separate because the entire finding is that
    they scale differently; one aggregate would hide the disagreement.
    """
    constants = constants if constants is not None else measure_constants(seed=seed)
    all_ciks = sorted(r["cik"] for r in fullindex.registry())
    if not all_ciks:
        raise ValueError(
            "The filer registry is empty, so there is no filing calendar to "
            "replay. Build it first: ledgerline registry"
        )
    payload: dict = {
        "measured_at": date.today().isoformat(),
        "mode": "replay",
        "window": {"start": start, "end": end},
        "registry_filers": len(all_ciks),
        "constants": constants,
        "bias": BIAS_NOTE,
        "sizes": {},
    }
    for size in sizes:
        if size >= len(all_ciks):
            picked, note = set(all_ciks), "whole registry (requested size exceeds it)"
        else:
            picked = set(random.Random(f"{seed}:{size}").sample(all_ciks, size))
            note = f"deterministic sample, seed {seed}"
        entry = replay(picked, start, end, constants)
        entry["sample"] = note
        payload["sizes"][str(size)] = entry
    return status.stamp(payload)


def persist_samples(payload: dict) -> int:
    """Commit the measurement to cost_samples, one row per size.

    A verification that lives only in a printed table is not a verification
    anyone can re-check later.
    """
    status.assert_stamped(payload)
    window = f"{payload['window']['start']}..{payload['window']['end']}"
    conn = edgar.db()
    with conn:
        for size, entry in payload["sizes"].items():
            conn.execute(
                "INSERT OR REPLACE INTO cost_samples "
                "(sample_id, measured_at, mode, universe_size, day, index_rows, "
                " universe_hits, requests, bytes_fetched, seconds, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"{payload['measured_at']}:{payload['mode']}:{size}",
                    payload["measured_at"], payload["mode"], int(size), window,
                    None,
                    entry["tier1_requests_per_day"]["p90"],
                    # p90-day totals: the peak is what capacity planning is for,
                    # and the median is preserved in the note beside it.
                    (entry["tier1_requests_per_day"]["p90"] or 0)
                    + entry["tier0_requests_per_run"],
                    entry["bytes_per_day"]["p90"],
                    (entry["seconds_per_day"] or {}).get("p90"),
                    json.dumps(entry),
                ),
            )
    conn.close()
    return len(payload["sizes"])


def report(payload: dict, path: str | None = None) -> str:
    """Write the full stamped payload to reports/cost.json (or `path`)."""
    status.assert_stamped(payload)
    if path is None:
        path = os.path.join(os.path.dirname(edgar.ROOT), "reports", "cost.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1)
    return path
