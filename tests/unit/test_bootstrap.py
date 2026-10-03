"""bootstrap.sh on a clone (ROADMAP_V1 1.0-d)."""
from __future__ import annotations

import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "bootstrap.sh")


def _script() -> str:
    with open(SCRIPT) as fh:
        return fh.read()


def _templates() -> dict[str, str]:
    s = _script()
    return {m.group(1): m.group(2) + "\n"
            for m in re.finditer(r"write_new (\S+) <<'EOF'\n(.*?)\nEOF", s, re.S)}


def test_bootstrap_never_overwrites_a_committed_file():
    """It used to `cat >` every config file on every run. On a clone those files
    had moved on from the templates (data/.gitignore, .gitignore, Makefile), so
    a stranger's first command would have reverted them."""
    s = _script()
    assert not re.search(r"^cat > ", s, re.M)
    assert "write_new" in s


def test_templates_match_the_committed_files():
    """A fresh-directory bootstrap must produce what the repo actually has."""
    for target, body in _templates().items():
        with open(os.path.join(REPO, target)) as fh:
            assert fh.read() == body, f"bootstrap.sh template for {target} has drifted"


def test_bootstrap_runs_the_python_it_finds_not_just_locates_it():
    """macOS ships python3 3.9, and a pyenv shim like python3.12 is on PATH but
    refuses to run unless selected. Finding a name isn't enough."""
    s = _script()
    assert "ok_python" in s and '"$PY" -m venv' in s


def test_bootstrap_is_valid_bash():
    assert subprocess.run(["bash", "-n", SCRIPT]).returncode == 0
