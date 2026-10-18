"""Golden tests: portable presupport patterns.

The Python validator (``rules.portable_pattern_error``) and the native one
accept the same patterns, and an accepted pattern flags the same members in
both rule implementations, including names that probe case folding and the
meaning of ``$``.
"""
import random

import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "portable_pattern_error"):
    pytest.skip("stl_native predates portable patterns; rebuild it", allow_module_level=True)

from app.crawler import rules

TOKENS = [
    "a", "S", "s", "k", "_", "-", " ", ".", r"\.", r"\d", r"\D", "[a-z]", "[^_]", r"[\-x]", "[_ -]", "[s]",
    "(", ")", "(?:", "(?<!", "(?<=", "(?=", "(?!", "|", "*", "+", "?", "*?", "{2}", "{1,3}", "{2,}", "^", "$",
    r"\Z", r"\A", "[[:alpha:]]", r"\w", r"\s", r"\b", "{,2}", "]", "}", "[]", "[a-]", "[-a]", "[a-z-0]",
    "(?P<n>", "(?i)", r"\1", "é", "&&", "[a&&b]", "[~~]", "stl", "sup", r"\$", r"\\", "{", "[Z-a]",
]
NAMES = [
    "body_presupported.stl", "x_SUPPORTED.stl", "sup_arm.stl", "S_leg.stl", "[s]_head.stl", "unsupported.stl",
    "a.stl\n", "a_sup.stl\n\n", "ſup_x.stl", "Key.stl", "İcon_sup.stl", "ß_sup.stl", "kk.stl", "12_ab.stl",
    "a-b.stl", "a b.stl", "_.stl", "Z[.stl", "^$.stl", "x\\y.stl", "aa.stl", "SUP_A.STL", "s_.stl", "tab\t.stl",
]


def _random_patterns(n):
    rng = random.Random(12)
    for _ in range(n):
        yield "".join(rng.choice(TOKENS) for _ in range(rng.randint(1, 6)))


def test_validators_agree_and_rules_match():
    accepted = 0
    for pattern in list(_random_patterns(4000)) + rules.DEFAULT_PRESUPPORT_PATTERNS:
        py_err = rules.portable_pattern_error(pattern)
        rs_err = stl_native.portable_pattern_error(pattern)
        assert (py_err is None) == (rs_err is None), (pattern, py_err, rs_err)
        if py_err is not None:
            continue
        accepted += 1
        names = [f"dir/{n}" for n in NAMES]
        for name in names:
            py = rules._detect_warnings_py([name, "a.jpg"], presupport_patterns=[pattern])
            rs = stl_native.detect_warnings([name, "a.jpg"], presupport_patterns=[pattern])
            assert rs == py, (pattern, name)
    assert accepted > 300


def test_custom_portable_list_runs_natively(monkeypatch):
    """A portable custom list goes to the native rules; a non-portable one stays in Python."""
    from app import native
    monkeypatch.delenv("STL_NATIVE", raising=False)
    native.reload()
    calls = []
    real = stl_native.detect_warnings
    monkeypatch.setattr(stl_native, "detect_warnings", lambda *a, **k: calls.append(1) or real(*a, **k))
    rules.detect_warnings(["x/a_sup.stl"], presupport_patterns=[r"_sup\.stl$"])
    assert calls == [1]
    rules.detect_warnings(["x/a_sup.stl"], presupport_patterns=[r"\w+_sup"])
    assert calls == [1]
