"""
The adapter seam: one Protocol, three implementations, injectable everywhere.

Why urllib and not a vendor SDK: the dependency budget is closed (the core
four only -- see pyproject.toml), and edgar.py already speaks HTTP through
urllib for the same reason. Both real clients POST the same Messages-API
request body and return the same Response, so run.py cannot tell them apart,
and a Trident endpoint drops in as a base-URL + auth swap.

Nothing here is constructed at import time and no environment variable is
read at import time: importing the package with ANTHROPIC_API_KEY and
TRIDENT_ENDPOINT both unset cannot raise, and every test injects a
ScriptedClient -- there is no code path in the test suite that constructs a
real client, so CI can never touch a network.

ScriptedClient RAISES IndexError when its queue is exhausted, so a test
expecting one call fails loudly if two are made -- that is how the cost gate
and the two-attempt cap are actually pinned rather than assumed.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

FALLBACK_MODEL = "claude-sonnet-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def default_model() -> str:
    """Read at call time, not import time, so tests and operators can swap
    the model without reloading the package."""
    return os.environ.get("LEDGERLINE_NARRATE_MODEL", FALLBACK_MODEL)


@dataclass(frozen=True)
class Response:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class NarrationClient(Protocol):
    def complete(self, *, system: str, messages: list[dict], schema: dict,
                 max_tokens: int = 2000) -> Response: ...


def _body(model: str, system: str, messages: list[dict], schema: dict,
          max_tokens: int) -> bytes:
    # output_config.format is the Messages API structured-output surface; the
    # provider constrains the response to the claim schema so most defects
    # are unproducible before the verifier ever runs. The verifier still
    # re-checks everything.
    return json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }).encode()


def _post(url: str, headers: dict[str, str], body: bytes,
          timeout: float) -> Response:
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    text = "".join(b.get("text", "") for b in data.get("content", []))
    usage = data.get("usage", {})
    return Response(text=text,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    model=data.get("model", ""))


class AnthropicClient:
    def __init__(self, *, api_key: str | None = None,
                 model: str | None = None, timeout: float = 60.0):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "No ANTHROPIC_API_KEY is set, so the narration service cannot "
                "be reached. Set it in the environment, or use --dry-run to "
                "see the payload without spending a credit.")
        self.api_key: str = key
        self.model = model or default_model()
        self.timeout = timeout

    def complete(self, *, system: str, messages: list[dict], schema: dict,
                 max_tokens: int = 2000) -> Response:
        return _post(
            ANTHROPIC_URL,
            {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION},
            _body(self.model, system, messages, schema, max_tokens),
            self.timeout)


class TridentClient:
    """Same request body, different address and auth: Trident is an internal
    Messages-compatible endpoint, not the Anthropic API."""

    def __init__(self, endpoint: str, *, token: str | None = None,
                 model: str | None = None, timeout: float = 60.0):
        self.endpoint = endpoint
        self.token = token or os.environ.get("TRIDENT_TOKEN")
        self.model = model or default_model()
        self.timeout = timeout

    def complete(self, *, system: str, messages: list[dict], schema: dict,
                 max_tokens: int = 2000) -> Response:
        headers = {}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        return _post(self.endpoint, headers,
                     _body(self.model, system, messages, schema, max_tokens),
                     self.timeout)


@dataclass
class ScriptedClient:
    """Test double: returns queued responses in order, records every call in
    .calls, raises a queued Exception to simulate a transport failure, and
    raises IndexError when exhausted -- a loud failure is the pin."""

    responses: list
    calls: list[dict] = field(default_factory=list)

    def complete(self, *, system: str, messages: list[dict], schema: dict,
                 max_tokens: int = 2000) -> Response:
        self.calls.append({"system": system, "messages": list(messages),
                           "schema": schema, "max_tokens": max_tokens})
        if not self.responses:
            raise IndexError("ScriptedClient exhausted: an unexpected extra "
                             "model call was made")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return Response(text=nxt, input_tokens=10, output_tokens=10,
                        model="scripted")


def build_client() -> NarrationClient:
    """Called ONLY from run.narrate() when no client was injected -- never at
    import. Prefers the internal endpoint when configured."""
    endpoint = os.environ.get("TRIDENT_ENDPOINT")
    if endpoint:
        return TridentClient(endpoint)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient()
    raise RuntimeError(
        "Narration needs a model service and none is configured: set "
        "TRIDENT_ENDPOINT (internal) or ANTHROPIC_API_KEY (Anthropic API), "
        "or use --dry-run to see the payload without one.")
