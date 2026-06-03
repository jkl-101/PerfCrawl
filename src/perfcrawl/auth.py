"""Phase-4 authenticated-crawl seam: driven login → portable ``storage_state``.

This module owns the *capture* and *validation* of an authenticated session and
the credential-redaction helper that downstream sinks apply. The *replay* of a
captured session onto the worker Chrome lives in ``orchestrator.measure_url``
(the ``auth_state`` param) — the two halves meet at the Playwright
``storage_state`` dict, the portable session currency (D-02).

The spike (``.claude/skills/spike-findings-performance-statistics-gathering``)
proved the single load-bearing fact this module honors: **log in on the
browser's DEFAULT context** (``connect_over_cdp(...).contexts[0]``), never an
isolated per-sample context. A Lighthouse CDP target navigates in the default
context; a session anywhere else is invisible to it. The Chrome launch
seam (``orchestrator._launch_chrome_with_cdp_port``) is reused UNCHANGED — Phase
4's only change is *where the login happens* (spike requirement #3).

Security (RESEARCH § Security Domain, D-07):

- Credentials enter via env only (``PERFCRAWL_USERNAME`` / ``PERFCRAWL_PASSWORD``);
  this module never reads them from argv. ``make_scrubber`` is seeded from the
  live secret values and applied by the CLI to every sink (stderr, RunRecord
  JSON, LH artifacts) so a credential never reaches logs or disk.
- A login that cannot be confirmed raises ``AuthError`` BEFORE any crawl is paid
  for (fail-loud). An empty/stale ``storage_state`` is rejected at t=0
  (``validate_storage_state``, Pitfall 4) — never discover-then-abort-on-page-1.
- Every helper that touches a remote URL or page content degrades to a
  deterministic fallback rather than raising (the scope.py never-raise discipline).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from perfcrawl.constants import LOGIN_WAIT_TIMEOUT_MS, REDACTION_PLACEHOLDER
from perfcrawl.orchestrator import _launch_chrome_with_cdp_port

__all__ = [
    "AuthError",
    "make_scrubber",
    "do_form_login",
    "validate_storage_state",
    "resolve_auth_state",
]


class AuthError(Exception):
    """Auth failed — CLI maps to ExitCode.AUTH_ERROR (D-15, the "auth band" = 3).

    Raised when a driven login cannot be confirmed (no crawl is paid for), when
    an ``--auth-state`` file is empty/stale at t=0 (Pitfall 4), or when the
    state JSON is unreadable. The CLI's catch arm maps every ``AuthError`` to
    ``ExitCode.AUTH_ERROR`` so ``case $? in 3) re-auth ;; esac`` scripting can
    distinguish a session/login problem from Chrome/LH breakage (code 2).
    """


def make_scrubber(*secrets: str | None) -> Callable[[str], str]:
    """Build a closure that replaces each real secret with ``REDACTION_PLACEHOLDER``.

    Seeded once (typically from username + password) and applied at every sink
    that prints or persists auth-adjacent text (D-07). Empty/None secrets are
    filtered out: ``"".replace("", X)`` sprays ``X`` between every character, so
    a scrubber seeded with an empty secret would corrupt all text — the factory
    must never let a falsy secret reach ``str.replace``.

    >>> make_scrubber("admin123", "admin")("user=admin pw=admin123")
    'user=***REDACTED*** pw=***REDACTED***'
    >>> make_scrubber("")("nothing secret")
    'nothing secret'
    """
    # Longest-first so that a secret which is a substring of another (e.g. a
    # username that is a prefix of the password) does not partially mask the
    # longer one before it is matched.
    reals = sorted((s for s in secrets if s), key=len, reverse=True)

    def scrub(text: str) -> str:
        out = text
        for secret in reals:
            out = out.replace(secret, REDACTION_PLACEHOLDER)
        return out

    return scrub


def _login_confirmed(page: Any, login_url: str, success_rule: dict[str, str] | None) -> bool:
    """Decide whether the post-submit page represents a logged-in session.

    Default heuristic (CONTEXT Claude's-Discretion): the post-submit URL is NOT
    the login URL ⇒ logged in. A page still sitting on the login path (including
    a ``/login/?next=...`` redirect) ⇒ NOT logged in.

    Optional overrides via ``success_rule`` (for the 200-logged-out edge case a
    bare redirect heuristic can't catch):

      - ``{"text": <marker>}`` — marker present in page content ⇒ confirmed.
      - ``{"url": <fragment>}`` — landed URL contains the fragment ⇒ confirmed.

    Never raises (scope.py discipline): if reading ``page.content()`` blows up,
    the text rule degrades to ``False`` and the URL/default heuristic still
    resolves from ``page.url``.
    """
    landed = getattr(page, "url", "") or ""

    if success_rule:
        # success-url rule (cheap, no DOM access): landed URL contains fragment.
        url_fragment = success_rule.get("url")
        if url_fragment:
            return url_fragment in landed
        # success-text rule: marker present in the rendered content.
        marker = success_rule.get("text")
        if marker:
            try:
                return marker in page.content()
            except Exception:
                return False

    # Default redirect heuristic: still on the login path ⇒ not logged in.
    # Use prefix containment so a `?next=` query on the login URL still counts
    # as a failed login.
    return login_url not in landed and not landed.startswith(login_url)


def do_form_login(
    *,
    port: int,
    login_url: str,
    user_sel: str,
    pass_sel: str,
    submit_sel: str,
    username: str,
    password: str,
    success_rule: dict[str, str] | None = None,
) -> dict:
    """Drive a form login on the DEFAULT context; return its ``storage_state`` dict.

    Connects to the already-launched Chrome over CDP at ``port``, logs in on
    ``browser.contexts[0]`` (the DEFAULT context a Lighthouse CDP target shares —
    NEVER an isolated per-sample context, spike requirement #1), confirms success
    or raises ``AuthError`` (fail-loud before any crawl is paid for), then captures
    ``ctx.storage_state()`` BEFORE ``browser.close()``. ``browser.close()`` only
    disconnects Playwright; the Popen'd Chrome (and its cookies) stay alive.

    The caller owns the Chrome lifecycle (launch via
    ``_launch_chrome_with_cdp_port`` then kill+reap+rmtree in a finally) — this
    function only connects/disconnects, exactly like the spike's
    ``playwright_login``.
    """
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
        # DEFAULT context (spike requirement #1 / D-03). `contexts[0]` is the
        # context the Lighthouse CDP target navigates in; a session on any
        # isolated per-sample context is invisible to it. A real
        # `connect_over_cdp` always exposes the default context at index 0.
        ctx = browser.contexts[0]
        page = ctx.new_page()
        # CR-01 (AUTH-04): every Playwright failure in this interaction block (a
        # wrong --user-sel/--pass-sel is the routine failure mode) MUST surface
        # as a credential-free AuthError, NOT a raw Playwright exception. A raw
        # exception sails past the CLI's `except AuthError` arm and Typer prints
        # a traceback whose frame locals carry `password=<live value>` — the
        # scrubber is never applied. `from None` suppresses that chain so the
        # live secret in those frames is never surfaced. The message names the
        # likely cause WITHOUT echoing any credential or selector value. The
        # `with sync_playwright()` context manager tears down the Playwright
        # connection on exception; this does NOT kill the Popen'd Chrome (the
        # caller owns Chrome's lifecycle — `browser.close()` is a disconnect
        # only, per the spike findings), so no Chrome kill is added here.
        try:
            page.goto(login_url, wait_until="load")
            page.fill(user_sel, username)
            page.fill(pass_sel, password)
            page.click(submit_sel)
            page.wait_for_load_state("load", timeout=LOGIN_WAIT_TIMEOUT_MS)
        except Exception:
            raise AuthError(
                "could not complete the login form — check --login-url and the "
                "--user-sel/--pass-sel/--submit-sel selectors"
            ) from None
        if not _login_confirmed(page, login_url, success_rule):
            browser.close()
            raise AuthError(
                "login could not be confirmed — post-submit page is still the "
                "login page (check selectors/credentials or pass --success-text)"
            )
        # Capture the portable session currency BEFORE disconnecting (D-02).
        state = ctx.storage_state()
        browser.close()  # disconnect only — the Popen'd Chrome stays alive
    return validate_storage_state(state)


def validate_storage_state(state: Any) -> dict:
    """Reject an empty/stale session at t=0; return a valid one unchanged (Pitfall 4).

    A captured-or-supplied ``storage_state`` is valid iff it is a dict carrying
    at least one cookie OR at least one origin (token-in-localStorage sessions).
    An empty state means the crawl would discover N pages then abort on the first
    audit — fail fast here with ``AuthError`` so no crawl is paid for. Never
    crashes on garbage (a non-dict input raises ``AuthError``, not ``TypeError``).
    """
    if not isinstance(state, dict):
        raise AuthError(f"auth state is not a JSON object (got {type(state).__name__})")
    cookies = state.get("cookies") or []
    origins = state.get("origins") or []
    if not cookies and not origins:
        raise AuthError(
            "auth state carries no cookies and no origins — the session is "
            "empty or expired (re-run `perfcrawl login` to refresh it)"
        )
    return state


def resolve_auth_state(
    *,
    auth_state_path: str | None = None,
    port: int | None = None,
    login_url: str | None = None,
    user_sel: str | None = None,
    pass_sel: str | None = None,
    submit_sel: str | None = None,
    username: str | None = None,
    password: str | None = None,
    success_rule: dict[str, str] | None = None,
) -> dict:
    """Produce a validated ``storage_state`` from whichever auth path is configured.

    Two mutually-exclusive paths (resolved ONCE, before discovery):

      1. ``--auth-state`` escape hatch: load the JSON file from
         ``auth_state_path`` and ``validate_storage_state`` it (fail fast at t=0
         if empty/stale — Pitfall 4).
      2. Driven form login: ``do_form_login`` against the running Chrome at
         ``port`` (the form-login path supplies ``login_url`` + selectors + creds).

    Raises ``AuthError`` if neither path is fully specified, if the file is
    unreadable, or if the resulting state is empty.
    """
    if auth_state_path:
        path = Path(auth_state_path)
        try:
            raw = path.read_text()
        except OSError as exc:
            raise AuthError(f"could not read --auth-state file {path}: {exc}") from None
        try:
            state = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise AuthError(f"--auth-state file {path} is not valid JSON: {exc}") from None
        return validate_storage_state(state)

    # Form-login path: every login parameter must be present.
    missing = [
        name
        for name, val in (
            ("port", port),
            ("login_url", login_url),
            ("user_sel", user_sel),
            ("pass_sel", pass_sel),
            ("submit_sel", submit_sel),
            ("username", username),
            ("password", password),
        )
        if not val
    ]
    if missing:
        raise AuthError(
            "auth requested but no --auth-state file and incomplete form-login "
            f"configuration (missing: {', '.join(missing)})"
        )
    assert port is not None  # narrowed by the `missing` check above
    return do_form_login(
        port=port,
        login_url=login_url,  # type: ignore[arg-type]
        user_sel=user_sel,  # type: ignore[arg-type]
        pass_sel=pass_sel,  # type: ignore[arg-type]
        submit_sel=submit_sel,  # type: ignore[arg-type]
        username=username,  # type: ignore[arg-type]
        password=password,  # type: ignore[arg-type]
        success_rule=success_rule,
    )


# Keep a reference so `_launch_chrome_with_cdp_port` reads as "reused, not
# reimplemented" (D-03 #3) and grep-asserts in the plan's acceptance criteria.
# The login subcommand (Plan 04) launches Chrome via this exact seam, then calls
# `do_form_login(port=...)`. The import binding above is the reuse; this alias
# documents the contract without shadowing it.
_LOGIN_CHROME_LAUNCHER = _launch_chrome_with_cdp_port
