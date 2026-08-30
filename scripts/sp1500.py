"""Fetch S&P 1500 constituents (500 + MidCap 400 + SmallCap 600).

Membership is not in any SEC dataset, so it comes from Wikipedia's constituent
tables. Cached to scripts/sp1500.json so a rerun is offline and the universe is
reproducible -- a universe that silently changes between runs would make the
case set unreproducible, which matters more here than freshness.

Note this is CURRENT membership, i.e. survivorship-biased at the index level:
names deleted from the index before today are absent. That biases the CONTROL
group optimistic (deleted names skew toward deterioration). Recorded here so the
Phase 0 write-up states it rather than discovers it.
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ledgerline import edgar  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sp1500.json")
PAGES = {
    "sp500": ("List_of_S%26P_500_companies", 0),
    "sp400": ("List_of_S%26P_400_companies", 0),
    "sp600": ("List_of_S%26P_600_companies", 0),
}
CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
TABLE = re.compile(r"<table[^>]*wikitable[^>]*>(.*?)</table>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")


def _text(cell: str) -> str:
    return TAG.sub("", cell).replace("&amp;", "&").strip()


def scrape(slug: str) -> list[str]:
    raw = edgar.fetch(f"https://en.wikipedia.org/wiki/{slug}", f"wiki/{slug}.html")
    html = raw.decode("utf-8", "replace")
    for table in TABLE.findall(html):
        rows = [[_text(c) for c in CELL.findall(r)] for r in ROW.findall(table)]
        if not rows or len(rows[0]) < 2:
            continue
        header = [h.lower() for h in rows[0]]
        col = next((i for i, h in enumerate(header) if "symbol" in h or "ticker" in h), None)
        if col is None:
            continue
        out = []
        for r in rows[1:]:
            if len(r) <= col:
                continue
            t = r[col].split("(")[0].strip().upper()
            # Wikipedia writes class shares as BRK.B; SEC's map uses BRK-B.
            if re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", t):
                out.append(t.replace(".", "-"))
        if len(out) > 100:
            return out
    return []


def main():
    all_t: dict[str, list[str]] = {}
    for name, (slug, _) in PAGES.items():
        t = scrape(slug)
        all_t[name] = t
        print(f"  {name}: {len(t)}")
    merged = sorted({t for v in all_t.values() for t in v})
    payload = {"indices": all_t, "tickers": merged, "n": len(merged)}
    with open(OUT, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"total unique: {len(merged)} -> {OUT}")


if __name__ == "__main__":
    main()
