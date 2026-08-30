"""
Restatement events from vintage growth.

Why this module exists: normalize() has carried the full vintage history on
every row since FINDINGS §5, and persist_metrics threw it away -- so nothing
downstream of sqlite could tell a first publication from a revision, and
finding a restatement meant re-normalizing gigabytes of cached JSON. The
vintages table keeps the history; this module diffs it and emits events.

Two measured findings drive the design:

  * Detection keys on VINTAGE-LIST GROWTH, not on amended forms. label.py's
    RESTATEMENT criterion scans for a form ending in "/A", and measured across
    12 cached filers that catches 6 of 624 revisions (0.96%) -- the other 99%
    arrive as revised comparatives inside ordinary 10-Ks and 10-Qs.
    on_amendment travels on the event as a flag, so the amended-form case is a
    labeled subset rather than the trigger. label.py itself is deliberately
    NOT changed: it is a criterion of the Phase 0 label set, and editing it
    after the holdout was scored would corrupt the one clean measurement the
    project has. Whether the label should use these events is a future
    re-measurement question.

  * rel_change divides by max(|prior|, |new|, 1.0), not |prior|. Against
    |prior| alone the measured p99 was 57.8 -- a sign flip or a near-zero
    prior makes the ratio meaningless, and a 57x "restatement" in an event
    feed is a bug report.

MATERIAL_REL and the measurements above come from 12 randomly sampled filers,
not the universe -- 624 revisions, 42.5% under 1% relative. PROVISIONAL until
a full-universe backfill re-derives them. Nothing is dropped either way: every
revision is written and `material` is a column, because filtering at write
time would destroy the denominator needed to say what fraction of revisions
matter.

Events are EMITTED, never applied: the superseded vintage row stays in
`vintages` at its original value and filed date. diff() must run against the
STORED vintages before persist_vintages() writes the new ones -- reversing the
order makes every revision look already-known. A test pins the ordering.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from . import edgar

# 1% relative change. Below it a revision is recorded but flagged immaterial:
# measured, 42.5% of revisions fall here -- rounding and reclassification.
# PROVISIONAL: set from a 12-filer sample, to be re-set from the first
# full-universe run.
MATERIAL_REL: float = 0.01


@dataclass
class Restatement:
    """One revision: the superseding vintage and the one it supersedes."""

    cik: str
    metric: str
    end_date: str
    kind: str
    filed: str
    prior_filed: str
    prior_value: float
    value: float
    rel_change: float
    form: str | None
    on_amendment: bool
    material: bool


def rel_change(prior: float, new: float) -> float:
    """Relative size of a revision, bounded against sign flips and near-zero
    priors by the max(|prior|, |new|, 1.0) denominator."""
    return abs(new - prior) / max(abs(prior), abs(new), 1.0)


def stored_vintages(conn: sqlite3.Connection,
                    cik: str) -> dict[tuple[str, str, str], list[dict]]:
    """(metric, end_date, kind) -> stored vintages, ascending by filed."""
    rows = conn.execute(
        "SELECT metric, end_date, kind, filed, value, form FROM vintages "
        "WHERE cik = ? ORDER BY metric, end_date, kind, filed",
        (cik,),
    ).fetchall()
    out: dict[tuple[str, str, str], list[dict]] = {}
    for metric, end, kind, filed, value, form in rows:
        out.setdefault((metric, end, kind), []).append(
            {"filed": filed, "value": value, "form": form}
        )
    return out


def diff(conn: sqlite3.Connection, cik: str, norm: dict) -> list[Restatement]:
    """Revisions in `norm` that the vintages table has not seen yet.

    Runs BEFORE persist_vintages: the stored list is the record of what was
    already known, and a vintage present there is not news. A period's very
    first vintage is a publication, not a revision, so a single-vintage period
    emits nothing; on a first ingest of a filer with history, every historical
    revision is emitted once, so the event table is the complete revision
    record rather than only what happened after we started watching.
    """
    stored = stored_vintages(conn, cik)
    events: list[Restatement] = []
    for metric, rows in norm.items():
        for r in rows:
            vints = r.get("vintages") or [r]
            if len(vints) < 2:
                continue
            key = (metric, r["end"], r.get("kind") or "")
            known = {v["filed"] for v in stored.get(key, [])}
            for prior, cur in zip(vints, vints[1:], strict=False):
                if cur.get("filed") in known:
                    continue  # this revision was already recorded
                if cur["value"] == prior["value"]:
                    continue  # a re-filing that changed nothing is not a revision
                form = cur.get("form")
                events.append(
                    Restatement(
                        cik=cik,
                        metric=metric,
                        end_date=r["end"],
                        kind=r.get("kind") or "",
                        filed=cur.get("filed") or "",
                        prior_filed=prior.get("filed") or "",
                        prior_value=float(prior["value"]),
                        value=float(cur["value"]),
                        rel_change=round(rel_change(prior["value"], cur["value"]), 6),
                        form=form,
                        on_amendment=form in edgar.AMENDED_FORMS,
                        material=rel_change(prior["value"], cur["value"]) >= MATERIAL_REL,
                    )
                )
    return events


def persist_vintages(conn: sqlite3.Connection, cik: str, norm: dict) -> int:
    """Write every vintage of every row. One executemany per filer -- the
    backfill over ~1,500 filers must not hold one giant transaction open."""
    payload = [
        (
            cik, metric, r["end"], r.get("kind") or "", v.get("filed") or "",
            float(v["value"]), v.get("form"), v.get("concept"), v.get("origin"),
            json.dumps(v.get("sources", [])),
        )
        for metric, rows in norm.items()
        for r in rows
        for v in (r.get("vintages") or [r])
    ]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO vintages "
            "(cik, metric, end_date, kind, filed, value, form, concept, origin, "
            " sources) VALUES (?,?,?,?,?,?,?,?,?,?)",
            payload,
        )
    return len(payload)


def record(conn: sqlite3.Connection, events: list[Restatement],
           run_id: int | None = None) -> int:
    """Append events. INSERT OR IGNORE plus a PK on the superseding vintage
    makes re-ingest idempotent for free -- a cron job that re-emits every
    restatement daily would be a broken feed."""
    if not events:
        return 0
    now = datetime.now(UTC).isoformat()
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO restatements "
            "(cik, metric, end_date, kind, filed, prior_filed, prior_value, value, "
            " rel_change, form, on_amendment, material, detected_run, detected_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (e.cik, e.metric, e.end_date, e.kind, e.filed, e.prior_filed,
                 e.prior_value, e.value, e.rel_change, e.form,
                 int(e.on_amendment), int(e.material), run_id, now)
                for e in events
            ],
        )
    return len(events)


def events(cik: str | None = None, since: str | None = None,
           material_only: bool = True) -> list[dict]:
    """Read recorded revisions back. `material_only=False` is what shows the
    42.5% sub-1% tail the default hides."""
    q = ("SELECT cik, metric, end_date, kind, filed, prior_filed, prior_value, "
         "value, rel_change, form, on_amendment, material FROM restatements")
    conds, args = [], []
    if cik:
        conds.append("cik = ?")
        args.append(cik)
    if since:
        conds.append("filed >= ?")
        args.append(since)
    if material_only:
        conds.append("material = 1")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY filed, cik, metric, end_date"
    conn = edgar.db()
    rows = conn.execute(q, args).fetchall()
    conn.close()
    cols = ("cik", "metric", "end_date", "kind", "filed", "prior_filed",
            "prior_value", "value", "rel_change", "form", "on_amendment",
            "material")
    return [dict(zip(cols, r, strict=True)) for r in rows]


def affected_periods(events_: list[Restatement]) -> set[tuple[str, str]]:
    """(cik, end_date) pairs a batch of events touches -- the join surface a
    future published-signal store needs to answer "would this restatement have
    changed a signal we emitted?". Phase 1 stops here because there is no
    published-signal store to join against yet."""
    return {(e.cik, e.end_date) for e in events_}


def as_dict(e: Restatement) -> dict:
    return asdict(e)
