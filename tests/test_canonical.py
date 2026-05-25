"""Canonical URL key tests — success criterion #4, D-01..D-05.

These assertions pin the observable transform of ``canonical_key(url)``:
tracking params dropped, query sorted, fragment dropped, trailing slash
stripped (except root), scheme+host lowercased, path case PRESERVED, default
ports stripped, percent-hex uppercased — and crucially, distinct pages are NOT
over-merged and malformed input never raises.
"""

import pytest

from perfcrawl.canonical import canonical_key


def test_variants_collapse():
    """Two URL spellings of one logical page collapse to a single key (D-02..D-05).

    scheme+host lowercased, utm_* dropped, query sorted, fragment dropped,
    trailing slash stripped, path case preserved (/Path stays /Path).
    """
    a = canonical_key("https://Example.com/Path/?utm_source=x&b=2&a=1#frag")
    b = canonical_key("https://example.com/Path?a=1&b=2")
    assert a == b
    # path case is preserved (paths are case-sensitive on most servers, D-02)
    assert "/Path" in a
    assert "/path" not in a


def test_tracking_params_dropped_functional_kept():
    """utm_*, gclid, fbclid removed; functional params (?id=5) kept (D-04)."""
    key = canonical_key(
        "https://x.com/p?utm_source=nl&utm_medium=email&gclid=abc&fbclid=def&id=5"
    )
    assert "utm_source" not in key
    assert "utm_medium" not in key
    assert "gclid" not in key
    assert "fbclid" not in key
    assert "id=5" in key


def test_query_sorted():
    """Remaining query params are sorted alphabetically (D-04)."""
    key = canonical_key("https://x.com/p?b=2&a=1")
    assert key.endswith("?a=1&b=2")


def test_fragment_dropped():
    """The #fragment never identifies a server resource — always dropped (D-05)."""
    with_frag = canonical_key("https://x.com/p#section-2")
    without = canonical_key("https://x.com/p")
    assert with_frag == without
    assert "#" not in with_frag


def test_trailing_slash_stripped_except_root():
    """Strip trailing slash except for root '/' (D-03)."""
    assert canonical_key("https://x.com/foo/") == canonical_key("https://x.com/foo")
    # root keeps its single slash
    root = canonical_key("https://x.com/")
    assert root.endswith("/")
    assert root == "https://x.com/"


def test_default_port_stripped():
    """:80 / :443 default ports removed (D-02)."""
    assert canonical_key("http://x.com:80/p") == canonical_key("http://x.com/p")
    assert canonical_key("https://x.com:443/p") == canonical_key("https://x.com/p")


def test_scheme_and_host_lowercased():
    """Scheme and host are lowercased; path case is preserved (D-02)."""
    key = canonical_key("HTTPS://Example.COM/MixedCasePath")
    assert key.startswith("https://example.com/")
    assert "/MixedCasePath" in key


def test_percent_hex_uppercased():
    """Percent-encoding hex is normalized to uppercase (D-02)."""
    # %2f lowercase -> %2F uppercase; both spellings must collapse.
    assert canonical_key("https://x.com/a%2fb") == canonical_key("https://x.com/a%2Fb")


def test_non_default_port_preserved():
    """A non-default port is part of identity and must be kept (D-02 only strips defaults)."""
    assert canonical_key("https://x.com:8443/p") != canonical_key("https://x.com/p")


def test_no_over_merge():
    """Genuinely distinct pages keep distinct keys (D-03/D-04, Pitfall 6)."""
    # functional pagination params are distinct pages
    assert canonical_key("https://x.com/?page=2") != canonical_key("https://x.com/?page=3")
    # www and apex are not merged
    assert canonical_key("https://www.x.com/") != canonical_key("https://x.com/")


def test_index_html_not_stripped():
    """index.html is a real resource and must NOT be stripped (D-03)."""
    assert canonical_key("https://x.com/index.html") != canonical_key("https://x.com/")


@pytest.mark.parametrize("bad", ["not a url", "", "://broken", "http://", "   "])
def test_malformed_input_does_not_raise(bad):
    """Malformed / non-URL input returns deterministically without raising (DoS mitigation)."""
    result = canonical_key(bad)
    assert isinstance(result, str)
    # deterministic: same input -> same output
    assert canonical_key(bad) == result


def test_idempotent():
    """Canonicalizing an already-canonical key is a no-op (stable self-join key)."""
    once = canonical_key("https://Example.com/Path/?utm_source=x&b=2&a=1#frag")
    twice = canonical_key(once)
    assert once == twice
