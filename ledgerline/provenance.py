"""
Accession traces: which filings a reading actually came from.

Why this module exists: the README promises "a score traces back to accessions
or it does not ship", and the code did not deliver it -- ZFlag published z,
baseline_median, baseline_scale, baseline_n and floored, and no way to find
the filing behind any of them. This module resolves the CURRENT quarter's
inputs for each fired flag back to their source accessions and labels the
reading TRACED / PARTIAL / UNTRACED. UNTRACED abstains.

Honest framing, not a data-quality win: measured, 0 of 21,032 stored rows lack
an accession, so the abstention fires on nothing today. It is a regression
guard on the README invariant, cheap to hold now while there is one scoring
surface.

Vintage-correctness is structural rather than re-checked: callers pass the
edgar.as_of() snapshot the gate itself scored, whose rows already carry the
vintage that was public at the cutoff (FINDINGS §5). A trace built from
anything else could cite a filing that did not yet exist at the cutoff, which
is a lookahead claim -- so reading_trace() takes the snapshot, never the full
normalized dict.

derived_fraction is SURFACED beside the measured universe distribution, not
judged. Derivation is the normal path -- roughly three quarters of every OCF
series exists only because derive.py differences YTD cumulatives -- so a
threshold that treated derived as degraded would abstain on exactly the data
the FINDINGS §2 fix was written to recover. DERIVED_FRACTION_HIGH sits above
the observed maximum as a tripwire for a filer genuinely unlike the rest, and
it labels, never suppresses.
"""
from __future__ import annotations

from collections.abc import Iterable

# Tripwire, above the observed maximum -- a reading past it is unlike every
# filer measured, which is worth a label and nothing more. PROVISIONAL: the
# distribution below comes from 34 backfilled filers, not the universe; a
# full-universe backfill should re-derive both.
DERIVED_FRACTION_HIGH: float = 0.50

DERIVED_FRACTION_OBSERVED: dict[str, float] = {
    "median": 0.294,
    "p95": 0.351,
    "max": 0.457,
    "n_filers": 34,
}


def row_trace(row: dict) -> dict:
    """One row reduced to where it came from."""
    return {
        "end": row.get("end"),
        "origin": row.get("origin"),
        "form": row.get("form"),
        "filed": row.get("filed"),
        "concept": row.get("concept"),
        "sources": [s for s in row.get("sources", []) if s],
    }


def trace(snap: dict, period: str, metrics: Iterable[str]) -> dict[str, dict]:
    """Each metric's row at `period` (or its latest row before it), traced.

    `snap` must be an as_of() snapshot -- see the module docstring. A metric
    with no row at or before the period traces to nothing, which the label
    treats as untraceable rather than guessing.
    """
    out: dict[str, dict] = {}
    for metric in metrics:
        rows = [r for r in snap.get(metric, []) if r.get("end", "") <= period]
        out[metric] = row_trace(rows[-1]) if rows else {"sources": []}
    return out


def reading_trace(snap: dict, period: str | None, flags: list[dict]) -> dict:
    """Per fired flag, the accession trace of the current-quarter inputs it
    consumed. Only the current quarter: the baseline's provenance is the
    baseline's own snapshots, and inlining ~20 of those per flag makes the
    payload unreadable without answering "where did THIS number come from"."""
    # Imported here, not at module top: signals_v3 imports this module, and the
    # inputs table lives there because it is the gate's own declaration of what
    # each diagnostic consumes.
    #
    # PROVENANCE_INPUTS, never DIAGNOSTIC_INPUTS. The latter is the coverage
    # gate's table -- which quarterly FLOW metrics must be complete enough to
    # score -- and keying the trace on it published a strict subset of the
    # accessions six of the thirteen diagnostics actually read, while label()
    # below still called the reading TRACED. It also made the UNTRACED
    # abstention unfalsifiable for those six: the untraceable input was not in
    # the list being checked.
    from .signals_v3 import PROVENANCE_INPUTS

    if not period:
        return {"period": None, "flags": {}}
    out: dict[str, dict] = {}
    for f in flags:
        name = (f.get("code") or "").lower()
        out[name] = trace(snap, period, PROVENANCE_INPUTS.get(name, ()))
    return {"period": period, "flags": out}


def label(reading: dict, derived_fraction: float) -> tuple[str, str | None]:
    """(label, abstain_reason). A fired flag none of whose inputs trace to any
    filing makes the reading UNTRACED, which abstains; a gap short of that is
    PARTIAL, and both PARTIAL and a high derived fraction are labels on a
    reading that still ships."""
    flags = reading.get("flags", {})
    any_gap = False
    for name, per_metric in flags.items():
        traces = list(per_metric.values())
        if traces and not any(t.get("sources") for t in traces):
            return "UNTRACED", (
                f"the figures behind the {name.replace('_', ' ')} flag cannot "
                "be traced to any filing -- a score that cannot cite its "
                "sources does not ship"
            )
        if any(not t.get("sources") for t in traces):
            any_gap = True
    return ("PARTIAL" if any_gap else "TRACED"), None
