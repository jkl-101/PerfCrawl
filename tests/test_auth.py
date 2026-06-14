"""Unit tests for the Phase-4 auth module (D-01/D-02/D-07, AUTH-01).

These pin the pure, Chrome-less surface of ``src/perfcrawl/auth.py``:

  - ``make_scrubber`` credential redaction (including the empty-secret no-op
    that must NEVER inject ``***REDACTED***`` between every character).
  - ``validate_storage_state`` fail-fast-at-t=0 (Pitfall 4): empty → AuthError,
    non-empty → passthrough.
  - ``_login_confirmed`` default redirect heuristic + the optional
    success-text / success-url override.
  - ``AuthError`` identity (CLI maps it to ``ExitCode.AUTH_ERROR`` = 3).

The real Chrome + Django form login is exercised by the e2e suite
(``tests/test_auth_e2e.py``), NOT here — these tests launch nothing. The
Playwright ``page`` for ``_login_confirmed`` is a tiny stub.
"""

from types import SimpleNamespace

import pytest

from perfcrawl.auth import (
    AuthError,
    _login_confirmed,
    capture_storage_state,
    do_form_login,
    make_scrubber,
    validate_storage_state,
)
from perfcrawl.constants import REDACTION_PLACEHOLDER

# ---------------------------------------------------------------------------
# make_scrubber (D-07 credential redaction)
# ---------------------------------------------------------------------------


def test_make_scrubber_redacts_each_secret():
    """Both the password and the username are replaced with the placeholder."""
    scrub = make_scrubber("admin123", "admin")
    assert (
        scrub("user=admin pw=admin123")
        == f"user={REDACTION_PLACEHOLDER} pw={REDACTION_PLACEHOLDER}"
    )


def test_make_scrubber_empty_secret_is_noop():
    """Empty/None secrets must not inject the placeholder between every char.

    ``"".replace("", X)`` in Python sprays X between every character — a naive
    scrubber seeded with an empty secret would corrupt all text. The factory
    must filter falsy secrets out entirely.
    """
    text = "nothing secret here"
    assert make_scrubber("")(text) == text
    assert make_scrubber(None)(text) == text
    assert make_scrubber(None, "")(text) == text
    # And it must not have injected the placeholder anywhere.
    assert REDACTION_PLACEHOLDER not in make_scrubber("", None)(text)


def test_make_scrubber_no_secrets_is_identity():
    """No secrets at all → identity scrub."""
    scrub = make_scrubber()
    assert scrub("user=admin pw=admin123") == "user=admin pw=admin123"


# ---------------------------------------------------------------------------
# validate_storage_state (Pitfall 4 — fail fast at t=0)
# ---------------------------------------------------------------------------


def test_validate_storage_state_passes_through_with_cookies():
    """A non-empty cookies list returns the dict unchanged."""
    state = {"cookies": [{"name": "sessionid", "value": "x"}], "origins": []}
    assert validate_storage_state(state) is state


def test_validate_storage_state_passes_through_with_origins():
    """Origins-only (token-in-localStorage) state is also valid."""
    state = {
        "cookies": [],
        "origins": [{"origin": "https://x", "localStorage": [{"name": "t", "value": "1"}]}],
    }
    assert validate_storage_state(state) is state


def test_validate_storage_state_empty_raises():
    """No cookies AND no origins → AuthError (stale/empty session, Pitfall 4)."""
    with pytest.raises(AuthError):
        validate_storage_state({"cookies": [], "origins": []})


def test_validate_storage_state_missing_keys_raises():
    """A dict missing both keys is rejected, not crashed on."""
    with pytest.raises(AuthError):
        validate_storage_state({})


def test_validate_storage_state_non_dict_raises():
    """Garbage (non-dict) input is rejected with AuthError, never an arbitrary crash."""
    with pytest.raises(AuthError):
        validate_storage_state(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _login_confirmed (default redirect heuristic + optional override)
# ---------------------------------------------------------------------------


def _fake_page(url: str, content: str = "") -> SimpleNamespace:
    """Tiny Playwright-page stub exposing ``.url`` and ``.content()``."""
    return SimpleNamespace(url=url, content=lambda: content)


def test_login_confirmed_redirect_away_from_login_is_true():
    """Default heuristic: post-submit URL != login URL ⇒ logged in."""
    page = _fake_page("https://site/dashboard/")
    assert _login_confirmed(page, "https://site/login/", success_rule=None) is True


def test_login_confirmed_still_on_login_is_false():
    """Default heuristic: post-submit URL == login URL ⇒ NOT logged in."""
    page = _fake_page("https://site/login/")
    assert _login_confirmed(page, "https://site/login/", success_rule=None) is False


def test_login_confirmed_login_url_prefix_is_false():
    """Landing on the login path (with a ?next= query) still counts as a failure."""
    page = _fake_page("https://site/login/?next=/dashboard/")
    assert _login_confirmed(page, "https://site/login/", success_rule=None) is False


def test_login_confirmed_other_host_substring_match_is_true():
    """WR-07: a different-host landing URL that incidentally CONTAINS the full
    login-URL string as a substring must still confirm the login.

    The old raw-string `login_url not in landed` heuristic mis-classified this
    as "still on login" (False) and aborted an actually-successful login. The
    path-based comparison confirms it: the landed PATH (/welcome) differs from
    the login PATH (/login), so the login is confirmed regardless of the host
    quirk.
    """
    # `landed` literally contains "https://site/login/" as a substring, but the
    # real landing path is /welcome on a different host.
    page = _fake_page("https://other.example/r?from=https://site/login/&to=/welcome")
    assert _login_confirmed(page, "https://site/login/", success_rule=None) is True


def test_login_confirmed_success_text_marker_in_content_is_true():
    """success_text rule: marker present in page content ⇒ True even if URL matches."""
    page = _fake_page("https://site/login/", content="<div>AUTHENTICATED_OK</div>")
    assert (
        _login_confirmed(page, "https://site/login/", success_rule={"text": "AUTHENTICATED_OK"})
        is True
    )


def test_login_confirmed_success_text_marker_absent_is_false():
    """success_text rule: marker absent ⇒ False."""
    page = _fake_page("https://site/dashboard/", content="<div>nope</div>")
    assert (
        _login_confirmed(page, "https://site/login/", success_rule={"text": "AUTHENTICATED_OK"})
        is False
    )


def test_login_confirmed_success_url_rule_matches():
    """success_url rule: landed URL contains the expected fragment ⇒ True."""
    page = _fake_page("https://site/app/home/")
    assert _login_confirmed(page, "https://site/login/", success_rule={"url": "/app/home/"}) is True


def test_login_confirmed_never_raises_on_garbage():
    """A page whose .content() blows up must not propagate — heuristic degrades."""

    def _boom() -> str:
        raise RuntimeError("DOM access failed")

    page = SimpleNamespace(url="https://site/dashboard/", content=_boom)
    # URL-based default heuristic still resolves without touching content.
    assert _login_confirmed(page, "https://site/login/", success_rule=None) is True


# ---------------------------------------------------------------------------
# AuthError identity
# ---------------------------------------------------------------------------


def test_auth_error_is_exception_subclass():
    assert issubclass(AuthError, Exception)


def test_auth_error_docstring_names_exit_code():
    """The exception docstring must name ExitCode.AUTH_ERROR (CLI-mapping contract)."""
    assert AuthError.__doc__ is not None
    assert "AUTH_ERROR" in AuthError.__doc__


# ---------------------------------------------------------------------------
# Redaction at every sink (D-07 / AUTH-04) — the scrubber removes the live
# username + password from a representative stderr string, a RunRecord JSON
# dump, and a saved Lighthouse HTML blob (Pitfall 3).
# ---------------------------------------------------------------------------


def test_redaction_scrubs_all_sinks():
    """One scrubber seeded from creds redacts stderr, RunRecord JSON, and LH HTML."""
    from datetime import UTC, datetime
    from uuid import UUID

    from perfcrawl.models import PageResult, RunRecord

    username, password = "admin", "admin123"
    scrub = make_scrubber(username, password)

    # 1) A stderr error string that echoes the creds.
    stderr_msg = f"auth failed: bad login for user={username} pw={password}"
    scrubbed_stderr = scrub(stderr_msg)
    assert username not in scrubbed_stderr
    assert password not in scrubbed_stderr
    assert REDACTION_PLACEHOLDER in scrubbed_stderr

    # 2) A RunRecord JSON dump whose target URL embeds the credential.
    run = RunRecord(
        id=UUID("3f1c2b9a-0000-4000-8000-0000000000d4"),
        started_at=datetime(2026, 6, 3, tzinfo=UTC),
        target=f"https://{username}:{password}@example.com/dashboard/",
        pages=[
            PageResult(
                url=f"https://{username}:{password}@example.com/dashboard/",
                url_key="https://example.com/dashboard/",
            )
        ],
    )
    scrubbed_json = scrub(run.model_dump_json(indent=2))
    assert password not in scrubbed_json
    assert username not in scrubbed_json
    assert REDACTION_PLACEHOLDER in scrubbed_json

    # 3) A saved Lighthouse HTML blob with the password rendered into a field.
    lh_html = f'<html><input id="password" value="{password}"></html>'
    scrubbed_html = scrub(lh_html)
    assert password not in scrubbed_html
    assert REDACTION_PLACEHOLDER in scrubbed_html


# ---------------------------------------------------------------------------
# do_form_login Playwright-interaction failure → credential-free AuthError
# (CR-01 / AUTH-04). A misconfigured selector raises a raw Playwright exception
# inside the goto/fill/click/wait block; that MUST surface as AuthError with no
# chain and no credential literal — not a raw traceback carrying password=...
# ---------------------------------------------------------------------------


class _FailingPage:
    """A fake Playwright page whose ``.fill`` raises (wrong-selector failure mode)."""

    def __init__(self):
        self.url = ""

    def goto(self, *_a, **_k):
        return None

    def fill(self, *_a, **_k):
        # Simulate a Playwright TimeoutError for a selector that never matched.
        # The message intentionally carries no credential — the point is the raw
        # exception TYPE is not AuthError and must be converted.
        raise RuntimeError("Timeout 5000ms exceeded waiting for selector")

    def click(self, *_a, **_k):  # pragma: no cover - never reached after fill raises
        return None

    def wait_for_load_state(self, *_a, **_k):  # pragma: no cover
        return None


class _FakeContext:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page

    def storage_state(self):  # pragma: no cover - never reached on the failure path
        return {"cookies": [{"name": "sid", "value": "x"}], "origins": []}


class _FakeBrowser:
    def __init__(self, page):
        self.contexts = [_FakeContext(page)]
        self.closed = False

    def close(self):
        self.closed = True


class _FakeSyncPlaywright:
    """Context-manager replacement for ``sync_playwright()`` returning a fake p."""

    def __init__(self, page):
        self._page = page

    def __enter__(self):
        page = self._page

        class _Chromium:
            def connect_over_cdp(self_inner, _endpoint):
                return _FakeBrowser(page)

        return SimpleNamespace(chromium=_Chromium())

    def __exit__(self, *_exc):
        return False


def test_do_form_login_playwright_failure_raises_credential_free_autherror(monkeypatch):
    """A failure inside the interaction block → AuthError, no chain, no creds (CR-01)."""
    sentinel_user = "SENTINEL_USER"
    sentinel_pass = "SENTINEL_PASS"

    failing_page = _FailingPage()
    monkeypatch.setattr(
        "perfcrawl.auth.sync_playwright",
        lambda: _FakeSyncPlaywright(failing_page),
    )

    with pytest.raises(AuthError) as exc:
        do_form_login(
            port=9222,
            login_url="https://site/login/",
            user_sel="#user",
            pass_sel="#pass",
            submit_sel="#submit",
            username=sentinel_user,
            password=sentinel_pass,
        )

    # The raw Playwright exception chain is suppressed (`from None`) so no
    # traceback with live-password frame locals is surfaced.
    assert exc.value.__cause__ is None
    # The friendly message echoes NO credential and NO selector value.
    msg = str(exc.value)
    assert sentinel_user not in msg
    assert sentinel_pass not in msg


def test_gitignore_covers_secrets():
    """``.gitignore`` ignores ``.env`` AND a saved auth-state pattern (D-07)."""
    from pathlib import Path

    gitignore = Path(__file__).resolve().parents[1] / ".gitignore"
    text = gitignore.read_text()
    assert ".env" in text, ".gitignore must ignore .env (credential env file)"
    assert "authstate" in text, ".gitignore must cover the saved storage_state pattern"


# ---------------------------------------------------------------------------
# capture_storage_state — robust capture over the Chrome-148 CDP cookie bug
# (DEBUG login-storage-state-capture)
# ---------------------------------------------------------------------------


class _FakeCDPSession:
    def __init__(self, cookies):
        self._cookies = cookies
        self.detached = False

    def send(self, method):
        assert method == "Network.getAllCookies"
        return {"cookies": self._cookies}

    def detach(self):
        self.detached = True


class _CaptureCtx:
    """A DEFAULT-context stub for capture_storage_state.

    ``storage_state()`` raises ``raise_exc`` (or returns ``ok_state`` when None).
    ``new_cdp_session`` hands back a page-scoped CDP session yielding ``cdp_cookies``.
    """

    def __init__(self, *, raise_exc=None, ok_state=None, cdp_cookies=None):
        self._raise = raise_exc
        self._ok = ok_state
        self._cdp_cookies = cdp_cookies or []
        self.cdp_session = None
        self.storage_state_calls = 0

    def storage_state(self):
        self.storage_state_calls += 1
        if self._raise is not None:
            raise self._raise
        return self._ok

    def new_cdp_session(self, _page):
        self.cdp_session = _FakeCDPSession(self._cdp_cookies)
        return self.cdp_session


class _CapturePage:
    def __init__(self, ls=None, origin="https://site"):
        self._ls = ls or {}
        self._origin = origin

    def evaluate(self, script):
        if "location.origin" in script:
            return self._origin
        return dict(self._ls)


def test_capture_storage_state_fast_path_returns_unchanged():
    """When ctx.storage_state() works, its result is returned verbatim and the
    page-scoped CDP fallback is NEVER touched (working headless path preserved)."""
    ok = {"cookies": [{"name": "sid", "value": "x"}], "origins": []}
    ctx = _CaptureCtx(ok_state=ok)
    out = capture_storage_state(ctx, _CapturePage())
    assert out is ok
    assert ctx.cdp_session is None  # fallback not entered


def test_capture_storage_state_falls_back_on_browser_context_error():
    """The exact Chrome-148 error triggers the page-scoped CDP cookie capture,
    producing a valid Playwright-shaped storage_state."""
    err = Exception(
        "BrowserContext.storage_state: Protocol error (Storage.getCookies): "
        "Browser context management is not supported."
    )
    cdp_cookies = [
        {
            "name": "sessionid",
            "value": "abc123",
            "domain": ".studyhalo.com",
            "path": "/",
            "expires": 1893456000.0,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        },
        # A session cookie (expires -1) with an unmapped sameSite to exercise
        # the normalizations.
        {
            "name": "csrftoken",
            "value": "xyz",
            "domain": "www.studyhalo.com",
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": True,
        },
    ]
    ctx = _CaptureCtx(raise_exc=err, cdp_cookies=cdp_cookies)
    page = _CapturePage(ls={"token": "t1"}, origin="https://www.studyhalo.com")

    state = capture_storage_state(ctx, page)

    # Fallback fired exactly once and detached its CDP session.
    assert ctx.storage_state_calls == 1
    assert ctx.cdp_session is not None and ctx.cdp_session.detached is True

    # Cookies mapped into Playwright shape.
    names = {c["name"] for c in state["cookies"]}
    assert names == {"sessionid", "csrftoken"}
    sess = next(c for c in state["cookies"] if c["name"] == "sessionid")
    assert sess["value"] == "abc123" and sess["secure"] is True
    assert sess["sameSite"] == "Lax"
    csrf = next(c for c in state["cookies"] if c["name"] == "csrftoken")
    assert csrf["expires"] == -1  # session-cookie sentinel preserved
    assert csrf["sameSite"] == "Lax"  # missing sameSite defaulted, not crashed

    # localStorage gathered as a state origin.
    assert state["origins"] == [
        {
            "origin": "https://www.studyhalo.com",
            "localStorage": [{"name": "token", "value": "t1"}],
        }
    ]

    # The product of the fallback validates as a real session (Pitfall 4).
    assert validate_storage_state(state) is state


def test_capture_storage_state_reraises_unrelated_error():
    """A storage_state() failure that is NOT the browser-context-management bug
    must propagate unchanged — the fallback is narrow, not a blanket swallow."""
    boom = RuntimeError("Target page, context or browser has been closed")
    ctx = _CaptureCtx(raise_exc=boom)
    with pytest.raises(RuntimeError, match="has been closed"):
        capture_storage_state(ctx, _CapturePage())
    assert ctx.cdp_session is None  # never reached the CDP fallback
