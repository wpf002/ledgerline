"""
Tier 4: machine-written prose about arithmetic that already happened.

Why this tier exists AFTER a KILL: the gate failed its pre-registered test on
2026-08-30 (28.7% caught against a required 60%; false alarms 3.83% per quiet
company-quarter against a 0.51% baseline). A narrated flag is therefore NOT a
warning, and prose is exactly the layer where a reader stops seeing the
arithmetic and starts seeing a conclusion. What ships instead is a
provenance-legible description: which measures moved, how far from this
company's own trailing median, on which filings -- verifiably faithful to the
numbers whether or not the numbers predict anything.

The hard constraint, pinned by test: no model output ever re-enters a scoring
decision. The model receives computed diagnostics, writes prose about them,
never computes a number and never decides whether to fire; a deterministic
verifier rejects any figure that does not trace to the payload, one repair
pass is allowed, and then the narration is refused -- the arithmetic ships
without prose rather than with unverified prose.

Re-export only: no logic, no client construction, no environment reads at
import, so importing this package with no credentials configured cannot fail.
"""
from .client import (
    NarrationClient,
    ScriptedClient,
    build_client,
)
from .run import (
    NarrationResult,
    fallback_text,
    narrate,
    narrate_batch,
    render,
    should_narrate,
)

__all__ = [
    "NarrationClient",
    "NarrationResult",
    "ScriptedClient",
    "build_client",
    "fallback_text",
    "narrate",
    "narrate_batch",
    "render",
    "should_narrate",
]
