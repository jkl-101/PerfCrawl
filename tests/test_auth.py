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


def test_gitignore_covers_secrets():
    """``.gitignore`` ignores ``.env`` AND a saved auth-state pattern (D-07)."""
    from pathlib import Path

    gitignore = Path(__file__).resolve().parents[1] / ".gitignore"
    text = gitignore.read_text()
    assert ".env" in text, ".gitignore must ignore .env (credential env file)"
    assert "authstate" in text, ".gitignore must cover the saved storage_state pattern"
