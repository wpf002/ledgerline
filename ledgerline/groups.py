"""
Named groups over the watchlist, and the one rule that makes them safe.

Why this module exists as more than two SQL statements: a filter that can
return nothing must always be able to say WHY it returned nothing. `--group
semi` (a typo for `semis`) and `--group semis` (real, but nobody has been put
in it yet) both produce an empty list of companies, and an empty list printed
without its reason reads as "none of your companies filed" -- the same defect
register as a score of 0.0 printed beside "could not assess". So `members()`
returns None for a group that does not exist and [] for one that exists and is
empty, and every caller is forced to tell them apart.

Deleting a group deletes memberships and never companies. The watchlist and
the filing histories fetched against it are the expensive thing; a group is a
label over them, and a label going away must not take data with it. There is
no code path from this module to a DELETE against `universe`.

Names are matched case-insensitively (the COLLATE NOCASE keys in edgar's
migration 3) and stored exactly as first typed: one group whichever way a
person spells it, still shown the way they chose to spell it.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import date

from . import edgar


@contextmanager
def _db(conn: sqlite3.Connection | None):
    """Borrow the caller's connection or open one and close it again.

    Same idiom as emit.load_signals: the bulk callers (publish, export) open
    one connection and reuse it across thousands of lookups, while a one-shot
    CLI call passes nothing and pays for its own.
    """
    own = conn is None
    conn = conn or edgar.db()
    try:
        yield conn
    finally:
        if own:
            conn.close()


def clean(name: str) -> str:
    """A group name with its surrounding and repeated whitespace removed.

    Not lowercased: case is the person's choice and the key already ignores
    it. Raises on a name that is empty once trimmed, because an unnameable
    group cannot be typed back into a --group flag afterwards.
    """
    out = " ".join((name or "").split())
    if not out:
        raise ValueError(
            "A group needs a name you can type back later, and this one is "
            "empty. Try something like: ledgerline groups --add semis"
        )
    return out


def exists(name: str, conn: sqlite3.Connection | None = None) -> bool:
    with _db(conn) as c:
        return c.execute("SELECT 1 FROM groups WHERE name = ?",
                         (clean(name),)).fetchone() is not None


def create(name: str, conn: sqlite3.Connection | None = None) -> bool:
    """Make a group. True if it was created, False if it already existed."""
    name = clean(name)
    with _db(conn) as c, c:
        cur = c.execute(
            "INSERT OR IGNORE INTO groups (name, created_at) VALUES (?, ?)",
            (name, date.today().isoformat()))
        return bool(cur.rowcount)


def delete(name: str, conn: sqlite3.Connection | None = None) -> int | None:
    """Remove a group and its memberships. Returns how many companies were in
    it, or None if there is no such group.

    The companies themselves are untouched -- see the module docstring. None
    rather than 0 so the caller can say "no such group" instead of "removed a
    group that was empty", which are different things a person did.
    """
    name = clean(name)
    with _db(conn) as c:
        if c.execute("SELECT 1 FROM groups WHERE name = ?",
                     (name,)).fetchone() is None:
            return None
        with c:
            n = c.execute("SELECT COUNT(*) FROM group_members WHERE name = ?",
                          (name,)).fetchone()[0]
            c.execute("DELETE FROM group_members WHERE name = ?", (name,))
            c.execute("DELETE FROM groups WHERE name = ?", (name,))
        return int(n)


def assign(name: str, ciks: Iterable[str],
           conn: sqlite3.Connection | None = None) -> dict[str, int]:
    """Put companies in a group, creating the group if it is new.

    Returns {"added": n, "already": m}. Assigning a company that is already in
    the group is not an error and is reported separately, because "nothing
    happened" and "12 companies added" are different outcomes and a person
    running the same import twice deserves to see which one they got.
    """
    name = clean(name)
    today = date.today().isoformat()
    added = already = 0
    with _db(conn) as c, c:
        c.execute("INSERT OR IGNORE INTO groups (name, created_at) VALUES (?, ?)",
                  (name, today))
        for cik in ciks:
            cur = c.execute(
                "INSERT OR IGNORE INTO group_members (name, cik, added_at) "
                "VALUES (?, ?, ?)", (name, cik, today))
            if cur.rowcount:
                added += 1
            else:
                already += 1
    return {"added": added, "already": already}


def unassign(name: str, ciks: Iterable[str],
             conn: sqlite3.Connection | None = None) -> int | None:
    """Take companies out of a group. Returns how many were removed, or None
    if there is no such group. The group survives becoming empty."""
    name = clean(name)
    with _db(conn) as c:
        if c.execute("SELECT 1 FROM groups WHERE name = ?",
                     (name,)).fetchone() is None:
            return None
        removed = 0
        with c:
            for cik in ciks:
                cur = c.execute(
                    "DELETE FROM group_members WHERE name = ? AND cik = ?",
                    (name, cik))
                removed += cur.rowcount
        return removed


def members(name: str, conn: sqlite3.Connection | None = None) -> list[str] | None:
    """The CIKs in a group, or None if the group does not exist.

    The None/[] distinction is the whole point of this module -- see the
    docstring. Callers that flatten it back into an empty list reintroduce the
    silent-empty-filter defect.
    """
    name = clean(name)
    with _db(conn) as c:
        if c.execute("SELECT 1 FROM groups WHERE name = ?",
                     (name,)).fetchone() is None:
            return None
        return [r[0] for r in c.execute(
            "SELECT cik FROM group_members WHERE name = ? ORDER BY cik",
            (name,)).fetchall()]


def listing(conn: sqlite3.Connection | None = None) -> list[dict]:
    """Every group with how many companies are in it, empty groups included.

    A LEFT JOIN and not an inner one: a group with no members is exactly the
    case a person needs to see, because it is the one that explains an empty
    filtered view.
    """
    with _db(conn) as c:
        rows = c.execute(
            "SELECT g.name, g.created_at, COUNT(m.cik) "
            "FROM groups g LEFT JOIN group_members m ON m.name = g.name "
            "GROUP BY g.name ORDER BY g.name COLLATE NOCASE").fetchall()
    return [{"name": r[0], "created_at": r[1], "n": r[2]} for r in rows]


def memberships(conn: sqlite3.Connection | None = None) -> dict[str, list[str]]:
    """cik -> the group names it belongs to, for every company in any group.

    One query for the whole watchlist: the export and the published watchlist
    both need this for ~1,500 companies, and 1,500 single-company lookups is
    the shape of query that turns a fast publish into a slow one.
    """
    out: dict[str, list[str]] = {}
    with _db(conn) as c:
        for cik, name in c.execute(
                "SELECT cik, name FROM group_members "
                "ORDER BY cik, name COLLATE NOCASE").fetchall():
            out.setdefault(cik, []).append(name)
    return out
