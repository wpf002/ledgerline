"""
SIC peer-set construction with a fallback ladder. Built and MEASURED here;
consumed by nothing, deliberately.

Why unwired: signals.peer_z has stayed unwired since v3 shipped, and it stays
that way permanently rather than "until a later phase" -- a peer overlay can
only remove fires, and fires missed (recall 0.287 against a required 0.60) is
what the gate failed on. Building the sets anyway is justified because the
measurement is true regardless of the gate: at 4-digit SIC only 45% of the
1,153 sector-admissible filers sit in a group with the 6 members peer_z
requires, versus 64% at 3-digit and 95% at 2-digit. A fixed 4-digit peer set
would abstain on more than half the universe without saying so, which is why
the fallback level travels on the PeerSet as provenance -- a 2-digit "peer
set" is a major group, not an industry, and a consumer is entitled to know
which it got.

NAMED POINT-IN-TIME EXCEPTION: SIC comes from the SEC submissions file, which
returns the CURRENT code -- a filer that reclassified gets its 2026 code
applied to a 2013 peer set. Fixing that means historical SIC from a second
ingestion path, the exact trap ROADMAP rejected for vendor fundamentals, so
the violation is carried visibly (sic_is_current=True on every PeerSet)
rather than pretended away. If anything ever consumes these sets, this is the
first thing it has to argue about.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from . import reasons
from .signals import MIN_PEERS

# Fallback ladder: industry -> industry group -> major group. Stops at the
# first level with MIN_PEERS members.
LEVELS: tuple[int, ...] = (4, 3, 2)


def sic_key(sic: str | int | None, level: int) -> str | None:
    """The first `level` digits of a 4-digit SIC, or None when there is no
    usable code. None, never a guess -- an unknown sector is a fact worth
    keeping."""
    if sic in (None, ""):
        return None
    try:
        code = int(str(sic))
    except ValueError:
        return None
    if not 0 <= code <= 9999:
        return None
    return f"{code:04d}"[:level]


@dataclass(frozen=True)
class PeerSet:
    """One filer's peer group, with the provenance a consumer would need to
    refuse it: the SIC level actually used and the standing point-in-time
    caveat."""

    cik: str
    level: int | None
    key: str | None
    members: tuple[str, ...]
    reason: str | None = None
    sic_is_current: bool = True

    def n(self) -> int:
        return len(self.members)

    def as_dict(self) -> dict:
        return asdict(self)


def peer_set(cik: str, sic_map: dict[str, str | None],
             scoreable: set[str] | None = None,
             min_peers: int = MIN_PEERS) -> PeerSet:
    """The peer group for one filer, walking the ladder until it is big enough.

    `scoreable` restricts membership to filers assessable at the cutoff --
    building the peer distribution from filers that only became assessable
    later is survivorship selection. Passed in as a set rather than computed
    here so the function stays pure and testable without a database. None
    means no restriction, which is only honest for a coarse census (the
    `peers` command says so when it uses it).

    Abstains with reasons.NO_PEER_SET when even the 2-digit major group is too
    thin -- an empty set with a reason, never a short list that peer_z would
    silently reject downstream.
    """
    own_sic = sic_map.get(cik)
    if sic_key(own_sic, 2) is None:
        return PeerSet(cik, None, None, (), reason=reasons.UNKNOWN_SECTOR)
    for level in LEVELS:
        key = sic_key(own_sic, level)
        members = tuple(sorted(
            c for c, s in sic_map.items()
            if c != cik                       # never its own peer
            and sic_key(s, level) == key
            and (scoreable is None or c in scoreable)
        ))
        if len(members) >= min_peers:
            return PeerSet(cik, level, key, members)
    return PeerSet(cik, None, None, (), reason=reasons.NO_PEER_SET)


def peer_sets(sic_map: dict[str, str | None],
              scoreable: set[str] | None = None) -> dict[str, PeerSet]:
    """Every filer's peer set. The coverage measurement the dashboard reports
    comes from counting `level` across these."""
    return {cik: peer_set(cik, sic_map, scoreable) for cik in sic_map}


def ladder_census(sets: dict[str, PeerSet]) -> dict[str, int]:
    """How many filers found a usable group at each level, and how many found
    none -- the 45%/64%/95% measurement, recomputed on whatever universe is
    actually loaded rather than quoted from the design sample."""
    out = {"4": 0, "3": 0, "2": 0, "none": 0, "unknown_sector": 0}
    for ps in sets.values():
        if ps.level is not None:
            out[str(ps.level)] += 1
        elif ps.reason == reasons.UNKNOWN_SECTOR:
            out["unknown_sector"] += 1
        else:
            out["none"] += 1
    return out
