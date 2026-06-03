"""Real-Chrome + real-Lighthouse e2e proof of the authenticated-audit seam (AUTH-01).

This is the single end-to-end test the spike fixture can prove: a driven Django
form login captures a Playwright ``storage_state``, that state is replayed onto
the worker Chrome's DEFAULT context (``browser.contexts[0]``), and a real
Lighthouse audit of ``/dashboard/`` inherits the session — landing on
``/dashboard/``, NOT redirected to ``/login/``.

It is the load-bearing D-02/D-03 reconciliation made observable:

  - login on ``contexts[0]`` (never an isolated context) ⇒ the LH CDP target sees
    the cookie;
  - ``measure_url(auth_state=...)`` runs on the default context (no
    ``new_context()``) ⇒ the session survives every sample;
  - ``run_record.final_displayed_url`` ends in ``/dashboard/`` ⇒ the audit
    captured the authenticated page, not the login redirect.

Marked ``e2e`` — opt-in only (needs Node ≥ 22.19 + Chrome + a working ``uv``);
skipped by default (``addopts = -m 'not e2e'``). Run explicitly with::

    uv run pytest tests/test_auth_e2e.py -x -q -m e2e
"""

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.e2e
def test_authenticated_audit_inherits_session(django_auth_fixture):
    """A Lighthouse audit of /dashboard/ inherits the form-login session (AUTH-01).

    End-to-end:
      1. Launch the worker Chrome on a CDP port (the unchanged D-03 seam).
      2. ``do_form_login`` on ``contexts[0]`` → a ``storage_state`` with cookies.
      3. ``measure_url(<dashboard>, auth_state=state)`` audits the page.
      4. Assert ``final_displayed_url`` ends in ``/dashboard/`` and NOT ``/login/``.
    """
    from perfcrawl.auth import do_form_login
    from perfcrawl.lighthouse_worker import preflight
    from perfcrawl.orchestrator import (
        _launch_chrome_with_cdp_port,
        measure_url,
    )

    base = django_auth_fixture.rstrip("/")
    login_url = f"{base}/login/?next=/dashboard/"
    dashboard_url = f"{base}/dashboard/"

    # The worker must be npm-installed for a real Lighthouse pass; skip cleanly
    # (rather than error) if this opt-in env lacks it.
    try:
        preflight()
    except Exception as exc:  # noqa: BLE001 — environment gate, not a test failure
        pytest.skip(f"lighthouse worker not available: {exc}")

    # Step 1: launch Chrome on a CDP port (reused UNCHANGED — D-03 #3).
    chrome, port, user_data_dir = _launch_chrome_with_cdp_port()
    try:
        # Step 2: drive the form login on the DEFAULT context → storage_state.
        auth_state = do_form_login(
            port=port,
            login_url=login_url,
            user_sel="#username",
            pass_sel="#password",
            submit_sel="#submit",
            username="admin",
            password="admin123",
        )
        # The captured session must carry the Django session cookie.
        assert auth_state.get("cookies"), "login captured no cookies"
    finally:
        # do_form_login only disconnects Playwright; the Popen'd Chrome stays
        # alive with its cookies. We MUST tear it down ourselves here because the
        # audit below launches its OWN Chrome (one-Chrome-per-worker invariant).
        import shutil
        import subprocess as _sp

        try:
            chrome.kill()
            try:
                chrome.wait(timeout=5)
            except _sp.TimeoutExpired:
                pass
        except Exception:
            pass
        shutil.rmtree(user_data_dir, ignore_errors=True)

    # Step 3: audit /dashboard/ with the captured session replayed onto the
    # audit Chrome's default context.
    run_record, _ = measure_url(
        url=dashboard_url,
        samples=1,
        emulation="mobile",
        auth_state=auth_state,
    )

    # Step 4: the decisive AUTH-01 signal.
    landed = run_record.final_displayed_url or ""
    assert landed.endswith("/dashboard/"), (
        f"expected the audit to land on /dashboard/, got {landed!r} "
        "(a /login/ landing means the session was NOT inherited)"
    )
    assert "/login/" not in landed, f"audit was redirected to login ({landed!r}) — session loss"
    assert run_record.auth_used is True


@pytest.mark.e2e
def test_no_creds_in_artifacts(django_auth_fixture, tmp_path, monkeypatch):
    """A full authenticated crawl leaves ZERO credential hits in output/ (Pitfall 3).

    The concrete AUTH-04 / D-07 grep guard: run ``perfcrawl crawl <url> --login-url
    ... --user-sel ...`` against the Django fixture with ``admin``/``admin123`` from
    env, let it write its output tree, then walk every file under output/ and assert
    the literal password ``admin123`` appears NOWHERE — not in result.json, not in
    a saved Lighthouse JSON/HTML artifact, not in the SQLite DB.
    """
    from typer.testing import CliRunner

    from perfcrawl.cli import app
    from perfcrawl.lighthouse_worker import preflight

    base = django_auth_fixture.rstrip("/")
    login_url = f"{base}/login/?next=/dashboard/"
    dashboard_url = f"{base}/dashboard/"
    password = "admin123"

    try:
        preflight()
    except Exception as exc:  # noqa: BLE001 — environment gate, not a test failure
        pytest.skip(f"lighthouse worker not available: {exc}")

    monkeypatch.setenv("PERFCRAWL_USERNAME", "admin")
    monkeypatch.setenv("PERFCRAWL_PASSWORD", password)

    output_dir = tmp_path / "output"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "crawl",
            dashboard_url,
            "--login-url",
            login_url,
            "--user-sel",
            "#username",
            "--pass-sel",
            "#password",
            "--submit-sel",
            "#submit",
            "--max-pages",
            "2",
            "--delay",
            "0",
            "--ignore-robots",
            "--output-dir",
            str(output_dir),
        ],
        catch_exceptions=False,
    )
    # The crawl must have produced an output tree (exit 0 success or 3 partial both
    # write artifacts; a USER/MEASUREMENT error would not prove the no-creds claim).
    assert result.exit_code in (0, 3), f"crawl exited {result.exit_code}\nSTDOUT:\n{result.stdout}"
    assert output_dir.exists(), "crawl wrote no output tree"

    # The decisive AUTH-04 check: zero password hits across the ENTIRE output tree.
    offenders = []
    for path in output_dir.rglob("*"):
        if not path.is_file():
            continue
        blob = path.read_bytes()
        if password.encode() in blob:
            offenders.append(str(path.relative_to(output_dir)))
    assert offenders == [], f"credential leaked into artifacts: {offenders}"

    # And the redaction placeholder proves the scrubber actually ran on a sink
    # (the login page's rendered password field, if captured, becomes REDACTED).
    # Not asserted hard (the dashboard audit may carry no form field), but the
    # zero-leak assertion above is the load-bearing guard.
