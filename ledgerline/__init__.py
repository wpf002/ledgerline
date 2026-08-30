"""Ledgerline Signal.

bootstrap.sh writes a .env and the README tells you to put a real contact
address in LEDGERLINE_UA, but nothing was reading that file -- edgar.py resolves
USER_AGENT from os.environ at import time, so a correctly-filled .env still
produced "LEDGERLINE_UA must be set". Loaded here, before any submodule import.

Real environment variables always win, so CI and cron are unaffected.
"""
from __future__ import annotations

import os

_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def load_dotenv(path: str = _ENV) -> None:
    """Minimal KEY=VALUE reader. No dependency, no interpolation, no export."""
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


load_dotenv()
