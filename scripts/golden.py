"""Regenerate the pinned scoring outputs in tests/fixtures/golden/.

Run this only after an INTENDED change to the scoring arithmetic, after bumping
signals_v3.GATE_VERSION. Say in the commit message what moved and why. See
ledgerline/validate/golden.py.

    python scripts/golden.py              regenerate golden.json
    python scripts/golden.py --fixtures   also rebuild the trimmed company files
                                          from the local cache (adding a company)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ledgerline.validate import golden  # noqa: E402

if "--fixtures" in sys.argv:
    print("rebuilt fixtures:", ", ".join(golden.build_fixtures()))
payload = golden.write_golden()
print(f"wrote {len(payload['cases'])} cases at gate {payload['gate_version']} "
      f"to {golden.GOLDEN_PATH}")
