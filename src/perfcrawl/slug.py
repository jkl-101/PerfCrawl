"""IN-02-safe URL key → filesystem-safe stem (D-07 / IN-02 boundary).

``page_slug(url_key, *, max_len=80)`` derives a filesystem-safe stem from a
canonical URL key. The url_key is an OPAQUE cross-run identity string —
``canonical.py`` warns that it can contain LITERAL ``..`` segments because w3lib
decodes percent-encoded dots (``%2e%2e``) without resolving them. Treating the
url_key as a path component would be a path-traversal vector; this function is
the documented sanitization boundary (D-07, IN-02 landmine from Phase 1 LEARNINGS).

Never raises on bizarre / malformed input — returns a deterministic ``"_"``
sentinel (Security Domain DoS mitigation; mirrors the ``canonical_key`` defensive
try/except + deterministic-fallback shape from Phase 1).

Specifically:

  - empty or whitespace-only input short-circuits to ``"_"`` BEFORE any work,
    because the empty stem is itself a path-injection hazard in some shells;
  - the rare input that makes urlsplit / regex raise falls back to ``"_"`` too;
  - the output is guaranteed to be a non-empty string in the safe charset
    ``[A-Za-z0-9._-]``, with no leading ``.`` (filesystem-hidden) and no ``/``
    or ``\\`` separators.

Inline-comment idiom mirrors the LEARNINGS surprise verbatim:
``# IN-02: w3lib decodes %2e%2e to literal '../' in url_key; this is the
documented sanitization boundary.``
"""

import re
from urllib.parse import urlsplit

# Anything outside [A-Za-z0-9._-] collapses to a single underscore.
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
# Runs of two or more dots collapse to ``__`` — the path-traversal protection.
# Even though we don't use the raw stem as a path right now, future renames or
# downstream consumers could; defense in depth at this boundary.
_DOTRUN = re.compile(r"\.{2,}")


def page_slug(url_key: str, *, max_len: int = 80) -> str:
    """Derive a filesystem-safe slug from a canonical URL key (D-07).

    The empty/blank sentinel is ``"_"`` (no valid slug is empty; a collision
    suffix appender — Phase 4 — handles uniqueness). Never raises.

    .. warning::

       This is the sole IN-02 sanitization boundary for url_key → filesystem
       path. Any code that constructs a path/filename from a url_key must call
       page_slug() first — never concatenate the raw key into a Path.
    """
    # WR-03-style empty short-circuit: a blank slug is not "no name", it's a
    # documented sentinel that the caller treats as "no real slug here".
    if not (url_key or "").strip():
        return "_"
    try:
        # IN-02: w3lib decodes %2e%2e to literal '../' in url_key; this is the
        # documented sanitization boundary.
        parts = urlsplit(url_key)
        # Drop scheme; combine netloc + path with '_' replacing '/'.
        stem = (parts.netloc + parts.path).replace("/", "_")
        # Collapse '..' (path-traversal protection — defense in depth even
        # though the immediate consumer doesn't use the stem as a path).
        stem = _DOTRUN.sub("__", stem)
        # Restrict to the safe filesystem charset.
        stem = _SAFE.sub("_", stem)
        # Strip leading/trailing separators-or-dots (filesystem hidden names).
        stem = stem.strip("._-") or "_"
        # Truncate to max_len so the resulting filename always fits typical
        # filesystem limits even after a collision suffix is appended.
        return stem[:max_len]
    except Exception:
        # Deterministic, never-raising fallback for non-URL / hostile input.
        return "_"
