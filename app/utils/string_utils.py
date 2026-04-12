"""
String similarity utilities used across the application.
"""

_SOUNDEX_CODE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "01230120022455012623010202",
)


def levenshtein(a: str, b: str) -> int:
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


def soundex(s: str) -> str:
    """Return the American Soundex code for *s* (4-character string)."""
    s = "".join(c for c in s.upper() if c.isalpha())
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
