"""CrawlConfig — the flag-value carrier for a crawl run (D-08..D-14).

A plain ``@dataclass`` (NOT a Pydantic model — it carries CLI flag values, it is
not part of the persisted data contract like ``RunRecord``). Every numeric/string
default reads from ``perfcrawl.constants`` so the "one editable place" discipline
holds: the CLI ``crawl`` command builds a ``CrawlConfig`` whose field defaults
trace back to the constants module, and the discovery BFS + measurement pass read
their tunables off the config object.

Critical invariant (Phase 1 LEARNINGS § "one editable place"): a field default is
NEVER an inlined literal — it is the imported ``DEFAULT_*`` constant. To retune a
default, edit ``constants.py``; this dataclass picks it up for free.
"""

from dataclasses import dataclass, field

from perfcrawl.constants import (
    DEFAULT_CONCURRENCY,
    DEFAULT_CRAWL_SAMPLES_N,
    DEFAULT_DENY_PATTERNS,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_PAGES,
    DEFAULT_MIN_DELAY_S,
    DEFAULT_QUERY_VARIANT_CAP,
)


@dataclass
class CrawlConfig:
    """Resolved crawl tunables for one ``perfcrawl crawl`` invocation.

    Numeric/string defaults come from ``constants.py`` (D-08/D-09/D-10); the
    boolean/list flags default to their conservative-posture values. The CLI
    layer (plan 03) overrides any field from a ``typer.Option``.
    """

    # --- crawl bounds (D-09, defaults from constants) ---
    max_pages: int = DEFAULT_MAX_PAGES
    max_depth: int = DEFAULT_MAX_DEPTH
    concurrency: int = DEFAULT_CONCURRENCY
    min_delay_s: float = DEFAULT_MIN_DELAY_S

    # --- trap defense (D-08) ---
    query_variant_cap: int = DEFAULT_QUERY_VARIANT_CAP

    # --- measurement (D-10: crawl defaults to 1 sample) ---
    samples: int = DEFAULT_CRAWL_SAMPLES_N
    emulation: str = "mobile"

    # --- filters (D-13/D-14: repeatable globs, exclude-wins, no-include=all) ---
    includes: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)

    # --- scope / policy toggles (D-06/D-07/D-11) ---
    include_subdomains: bool = False
    use_sitemap: bool = True
    ignore_robots: bool = False

    # --- D-04: list-only discovery, no measurement ---
    dry_run: bool = False

    # --- D-05: always-on destructive-link denylist (substring, case-insensitive) ---
    # Default traces to DEFAULT_DENY_PATTERNS (constants.py — the one editable place;
    # NEVER inline the literal set). field(default_factory=...) mirrors the mutable-list
    # pattern of includes/excludes above so each CrawlConfig gets its own list and a
    # ``--deny`` extension can append without mutating the shared built-in set.
    deny_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_PATTERNS))

    # --- D-01: authenticated-crawl inputs (login form + session-state handoff) ---
    # Carried on the config so the single admission path can exclude the login URL
    # (AUTH-04/D-07) and the measurement pass can drive the form login / replay a
    # saved storage_state. All default None (public crawl) — set by the CLI layer.
    login_url: str | None = None  # AUTH-04/D-07: excluded from the audited set
    user_sel: str | None = None  # CSS selector for the username field
    pass_sel: str | None = None  # CSS selector for the password field
    submit_sel: str | None = None  # CSS selector for the submit control
    auth_state_path: str | None = None  # path to a saved Playwright storage_state JSON
    success_text: str | None = None  # optional login-success marker text
    success_url: str | None = None  # optional login-success landing URL
