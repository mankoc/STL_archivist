"""
String similarity utilities used across the application.
"""
import unicodedata

_SOUNDEX_CODE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "01230120022455012623010202",
)


def levenshtein(a: str, b: str) -> int:
    """Return the Levenshtein (edit) distance between strings *a* and *b*.

    Computes the minimum number of single-character insertions, deletions, or
    substitutions required to transform *a* into *b*.  Uses an O(min(|a|,|b|))
    space iterative algorithm.

    Args:
        a: First string.
        b: Second string.

    Returns:
        Non-negative integer distance.  Zero means the strings are identical.
    """
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


def levenshtein_within(a: str, b: str, max_distance: int) -> bool:
    """Return True if the Levenshtein distance between *a* and *b* is ``<= max_distance``.

    Much faster than computing the full distance when only a threshold check is
    needed: it bails out early as soon as the distance cannot possibly stay within
    *max_distance*. Two cheap rejections come first — a length difference greater
    than *max_distance* already implies a larger distance — then a banded DP that
    only fills the diagonal of width ``2*max_distance+1`` and aborts the moment the
    whole current row exceeds the threshold.
    """
    if a == b:
        return True
    if len(a) < len(b):
        a, b = b, a
    la, lb = len(a), len(b)
    if la - lb > max_distance:
        return False

    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        # Only cells within the band |i-j| <= max_distance can be <= threshold.
        lo = max(1, i - max_distance)
        hi = min(lb, i + max_distance)
        if lo > 1:
            curr.extend([max_distance + 1] * (lo - 1))
        row_min = curr[-1]
        for j in range(lo, hi + 1):
            cb = b[j - 1]
            left = curr[j - 1] + 1
            up = prev[j] + 1 if j < len(prev) else max_distance + 1
            diag = prev[j - 1] + (ca != cb)
            val = min(left, up, diag)
            curr.append(val)
            if val < row_min:
                row_min = val
        # Pad the remainder of the row so prev[j] lookups stay valid next round.
        if len(curr) <= lb:
            curr.extend([max_distance + 1] * (lb + 1 - len(curr)))
        if row_min > max_distance:
            return False
        prev = curr
    return prev[lb] <= max_distance


def levenshtein_pairs(values: list[str], max_distance: int) -> list[tuple[int, int, int]]:
    """All ``(i, j, distance)`` with ``i < j`` and distance <= *max_distance*.

    Sorted by ``(i, j)``, the order of a nested ``for i … for j > i`` loop;
    distance 0 (equal strings) is included, so callers filter as they need.
    Uses the native core when available (``rust/stl-core/src/strings.rs``, all
    cores, banded DP) and falls back to the pure-Python loop.
    """
    from app import native
    mod = native.module()
    if mod is not None:
        try:
            return mod.levenshtein_pairs(list(values), int(max_distance))
        except (TypeError, ValueError, OverflowError, UnicodeError):
            pass   # e.g. a negative threshold or a lone surrogate: Python path
    return _levenshtein_pairs_py(values, max_distance)


def _levenshtein_pairs_py(values: list[str], max_distance: int) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for i, a in enumerate(values):
        for j in range(i + 1, len(values)):
            b = values[j]
            if abs(len(a) - len(b)) <= max_distance and levenshtein_within(a, b, max_distance):
                out.append((i, j, levenshtein(a, b)))
    return out


def soundex(s: str) -> str:
    """Soundex code of *s*, natively when possible (see :func:`_soundex_py`)."""
    from app import native
    mod = native.module()
    if mod is not None:
        try:
            return mod.soundex(s)
        except (ValueError, TypeError, UnicodeError, AttributeError):
            pass
    return _soundex_py(s)


def _soundex_py(s: str) -> str:
    """Return the American Soundex code for *s* (4-character string).

    Accented / non-ASCII letters are decomposed to their ASCII base before
    encoding, so names like "Müller" and "Muller" produce the same code.

    Args:
        s: Input string (any language).

    Returns:
        A 4-character Soundex code such as ``"M460"``.  Returns ``"0000"``
        for strings that contain no ASCII alphabetic characters.
    """
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s.upper() if c.isascii() and c.isalpha())
    if not s:
        return "0000"
    first = s[0]
    digits = s.translate(_SOUNDEX_CODE)
    result = first
    prev = digits[0]
    for digit in digits[1:]:
        if digit != "0" and digit != prev:
            result += digit
        if digit != "0":
            prev = digit
        if len(result) == 4:
            break
    return result.ljust(4, "0")
