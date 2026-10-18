"""
Scraping rules applied to archive member names during crawl.
"""
import logging
import re
from pathlib import Path

from app.constants import ARCHIVE_SUFFIXES, IMAGE_SUFFIXES

_PRINTABLE_SUFFIXES = frozenset({".stl", ".obj", ".lys", ".lyt", ".chitubox", ".ctp"})

logger = logging.getLogger(__name__)


# Matches `body_supported`, `arm-supported`, `torso supported`. Used as the
# stem-level test for the "Presupported" classification.
_PRESUPPORTED_RE     = re.compile(r"[_ -]supported", re.IGNORECASE)

# Captures the integer in patterns like `28mm`, `32 mm`, `75_mm`, and also when
# bounded by underscores (e.g. `Hero_32mm_Base`). `\b` can't be used because the
# underscore is a word character, so `_32mm_` has no word boundaries — instead we
# guard the number start with a non-digit lookbehind and the `mm` end with a
# non-alphanumeric lookahead.
_SCALE_MM_RE         = re.compile(r"(?<!\d)(\d+)[_ ]?mm(?![A-Za-z0-9])", re.IGNORECASE)

# Captures the denominator in patterns like `1/35`, `1-72` (also underscore-bounded).
_SCALE_RATIO_RE      = re.compile(r"(?<!\d)1[/\-](\d+)(?![A-Za-z0-9])")

# Filename patterns that mark an STL as presupported, applied case-insensitively
# to the bare filename of every ``.stl`` member.  Editable in
# Configuration → Crawler warnings; this list is the default:
#   · [_ -]presupported     — "body_presupported.stl", "arm - Presupported.stl"
#   · [ _-]supported.stl    — separator before "supported", not preceded by "un"
#   · sup_*.stl            — starts with "sup_"
#   · s_*.stl / [s]_*.stl  — starts with "s_" or "[s]_"
# "unsupported.stl" and "superman.stl" are excluded, and the negation veto in
# _is_presupported_member() rejects raw meshes these patterns might still reach.
DEFAULT_PRESUPPORT_PATTERNS: list[str] = [
    r"[_ -]presupported",
    r"(?<!un)[ _-]supported\.stl$",
    r"^sup_.*\.stl$",
    r"^(?:s|\[s\])_.*\.stl$",
]

#: The default as it stood while these patterns drove the retired "Archive with
#: supports" warning and nothing else.  A saved list identical to this was inert
#: config, so :func:`app.ui.config_dialog.get_presupport_patterns` upgrades it to
#: the current default rather than silently dropping the "presupported" rule.
LEGACY_PRESUPPORT_PATTERNS: list[str] = [
    r"(?<!un)[ _-]supported\.stl$",
    r"^sup_.*\.stl$",
    r"^(?:s|\[s\])_.*\.stl$",
]

_DEFAULT_PRESUPPORT_RE = re.compile(
    "|".join(f"(?:{p})" for p in DEFAULT_PRESUPPORT_PATTERNS),
    re.IGNORECASE,
)


def compile_presupport_patterns(patterns: "list[str] | None") -> list:
    """Compile presupport filename patterns, skipping invalid ones.

    ``None`` falls back to the module-level alternation of the defaults, which
    is already compiled — no need to rebuild it per call.
    """
    if patterns is None:
        return [_DEFAULT_PRESUPPORT_RE]
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            logger.warning("Invalid presupport pattern %r — skipped", pattern)
    return compiled


# Folder names that mark their contents as presupported.  Compared as whole
# tokens, not substrings: a segment is split on non-alphanumerics and flagged
# when any token appears here.  "Presupported STL" -> {presupported, stl} hits;
# "PS Files" -> {ps, files} hits; "Props" -> {props} and "PSD" -> {psd} do not,
# which a plain substring test on "ps" would have got wrong.
_PRESUPPORT_DIR_TOKENS = frozenset({
    "ps", "presup", "presups", "presupport", "presupports", "presupported",
    "sup", "sups", "support", "supports", "supported",
})

# Words that flip the meaning of the marker that follows them, so "No_Supported",
# "Un-Supported", "Without Supports" and "No PS" read as raw meshes.
_PRESUPPORT_NEGATIONS = frozenset({"un", "no", "non", "not", "without", "wo"})

# Merged spellings of the same idea, which tokenise as a single word and so are
# never preceded by a separate negation: "Unsupported", "NSFW_Unsup", "NoSupports".
_UNSUPPORTED_TOKENS = frozenset({
    "unsup", "unsupp", "unsupport", "unsupports", "unsupported",
    "nosup", "nosupp", "nosupport", "nosupports", "nosupported",
    "nonsupported", "notsupported", "nops",
})

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")

#: Results of :func:`_segment_support_state`.
_MARKED = "presupported"
_NEGATED = "unsupported"


def _segment_support_state(segment: str) -> "str | None":
    """Classify one path segment as :data:`_MARKED`, :data:`_NEGATED` or ``None``.

    The segment is tokenised on non-alphanumerics and read *in order*, which is
    what separates "Supported" from "No_Supported" while keeping "Dino Supported"
    working — a blunt "contains no/un" test would reject that. Whole tokens are
    compared, never substrings, so "PSD" and "Props" are not mistaken for "PS".

    Negation wins within a segment, so an oddity like "supports_unsupported"
    resolves to :data:`_NEGATED` rather than depending on token order.
    """
    tokens = [tok for tok in _TOKEN_SPLIT_RE.split(segment.lower()) if tok]
    marked = False
    for i, tok in enumerate(tokens):
        if tok in _UNSUPPORTED_TOKENS:
            return _NEGATED
        if tok in _PRESUPPORT_DIR_TOKENS:
            if i and tokens[i - 1] in _PRESUPPORT_NEGATIONS:
                return _NEGATED
            marked = True
    return _MARKED if marked else None


def _is_presupported_member(member: Path, name_res: list) -> bool:
    """True when *member* is a presupported mesh file.

    The member must be an ``.stl``; that gate is what keeps the warning honest,
    since the name patterns match any string. A preview image called
    ``Dragon - Presupported.jpg``, or the *directory* entry
    ``Biker Mice from Mars Presupported lys`` that an archive lists as a member
    of its own, used to raise "Models with presupported stl" on archives holding
    no presupported mesh at all.

    An "unsupported" marker then vetoes, wherever it appears — in any folder
    above the file or in the filename itself. Releases routinely nest the raw
    meshes *inside* the supported tree
    (``supports/Unsupported/120MM_Unsup_Emerald.stl``), so the outermost marker
    cannot be the one that decides; a veto anywhere is both simpler to predict
    than "nearest marker wins" and errs toward not flagging.

    Failing a veto, the member qualifies when a folder above it is marked
    (``Presupported/body.stl``, ``PS/arm.stl``, ``Supported STL/leg.stl``) or its
    own filename matches one of *name_res* (``body_presupported.stl``,
    ``sup_arm.stl``).

    Args:
        member: Member path inside the archive, with ``/`` separators.
        name_res: Compiled filename patterns from
            :func:`compile_presupport_patterns`.
    """
    if member.suffix.lower() != ".stl":
        return False

    dir_states = [_segment_support_state(part) for part in member.parent.parts]
    if _NEGATED in dir_states or _segment_support_state(member.stem) == _NEGATED:
        return False
    if _MARKED in dir_states:
        return True
    return any(rx.search(member.name) for rx in name_res)


def _detect_presupport_py(names: list[str]) -> str:
    """Return the presupport classification for an archive given its member *names*.

    Rules are applied in priority order (first match wins):

    1. **Lychee** — archive contains a ``.lys`` or ``.lyt`` slicer file.
    2. **Chitubox** — archive contains a ``.chitu``, ``.chitubox``, or ``.ctp`` slicer file.
    3. **Presupported** — archive contains an ``.stl`` file whose stem matches
       ``[_ -]supported`` (e.g. ``body_supported.stl``, ``arm-supported.stl``).
    4. **No** — none of the above criteria matched.

    Args:
        names: List of archive member names (full paths as returned by the
            archive library).

    Returns:
        One of the strings ``"Lychee"``, ``"Chitubox"``, ``"Presupported"``,
        or ``"No"``.
    """
    has_chitu        = False
    has_presupported = False

    for name in names:
        p      = Path(name.replace("\\", "/"))
        suffix = p.suffix.lower()

        if suffix in (".lys", ".lyt"):              # rule 1 – Lychee (highest priority)
            logger.debug("  Presupport=Lychee (matched %r)", name)
            return "Lychee"
        if suffix in (".chitu", ".chitubox", ".ctp"):  # rule 2 – Chitubox
            logger.debug("  Presupport candidate Chitubox (matched %r)", name)
            has_chitu = True
        if suffix == ".stl" and _PRESUPPORTED_RE.search(p.stem):  # rule 3
            logger.debug("  Presupport candidate Presupported (matched stem %r)", p.stem)
            has_presupported = True

    if has_chitu:
        logger.debug("  Presupport=Chitubox")
        return "Chitubox"
    if has_presupported:
        logger.debug("  Presupport=Presupported")
        return "Presupported"
    logger.debug("  Presupport=No")
    return "No"                               # rule 4 – fallback


# ── Warning texts ────────────────────────────────────────────────────────────
# Stored verbatim in ``STLModel.warning`` (newline-separated).  Named here so
# code that edits an existing warning string — rather than recomputing it from
# scratch — matches what the crawler wrote.
WARNING_PRESUPPORTED  = "Models with presupported stl"
WARNING_NO_IMAGES     = "Archive without images"
WARNING_NESTED        = "Nested archives"
WARNING_NO_PRINTABLE  = "Archive without printable files"
#: Retired in favour of WARNING_PRESUPPORTED, which covers the same idea with
#: folder awareness and the unsupported veto.  Kept only so stored records
#: carrying the old line can be recognised and cleared.
RETIRED_WARNING_SUPPORTS = "Archive with supports"
# Appended by the crawler itself rather than by detect_warnings().
WARNING_LOW_COMPRESSION = "Archive with low compression rate"
WARNING_UNREADABLE      = "Unreadable archive"

#: Canonical order of warning lines, so a warning edited in place still reads
#: the same way one written by a fresh crawl does.
WARNING_ORDER: list[str] = [
    WARNING_PRESUPPORTED,
    WARNING_NO_IMAGES,
    WARNING_NESTED,
    WARNING_NO_PRINTABLE,
    WARNING_LOW_COMPRESSION,
    WARNING_UNREADABLE,
]


def sort_warning_lines(lines: list[str]) -> list[str]:
    """Return *lines* in :data:`WARNING_ORDER`, unrecognised ones kept at the end."""
    return sorted(lines, key=lambda l: (WARNING_ORDER.index(l)
                                        if l in WARNING_ORDER else len(WARNING_ORDER)))


def _explain_warnings_py(
    names: list[str],
    check_presupported: bool = True,
    check_no_images: bool = True,
    check_nested_archive: bool = True,
    check_no_printable: bool = True,
    presupport_patterns: "list[str] | None" = None,
) -> "dict[str, list[str]]":
    """Return ``{warning: [member names that triggered it]}`` for *names*.

    Answers "why is this archive flagged?" using the exact rules
    :func:`detect_warnings` applies — the two must agree, so the per-rule tests
    live in one place and both call them.  The keys are the same warning strings
    ``detect_warnings`` would emit, in :data:`WARNING_ORDER`.

    Warnings that fire on an *absence* (no images, no printable files) map to an
    empty list: nothing in the archive triggered them, the lack of anything did.
    Callers should present those differently from "matched nothing".

    Args:
        names: Archive member names, as passed to :func:`detect_warnings`.
        check_presupported, check_no_images, check_nested_archive,
        check_no_printable, presupport_patterns:
            Same meanings as in :func:`detect_warnings`.

    Returns:
        Mapping of warning text to the member names responsible, ordered by
        :data:`WARNING_ORDER`.  A warning that does not apply is absent.
    """
    name_res = compile_presupport_patterns(presupport_patterns) if check_presupported else []

    presupported: list[str] = []
    nested:       list[str] = []
    has_image     = False
    has_printable = False

    for name in names:
        p = Path(name.replace("\\", "/"))
        suffix = p.suffix.lower()

        if check_no_images and suffix in IMAGE_SUFFIXES:
            has_image = True
        if check_no_printable and suffix in _PRINTABLE_SUFFIXES:
            has_printable = True
        if check_presupported and _is_presupported_member(p, name_res):
            presupported.append(name)
        if check_nested_archive and suffix in ARCHIVE_SUFFIXES:
            nested.append(name)

    result: dict[str, list[str]] = {}
    if check_presupported and presupported:
        result[WARNING_PRESUPPORTED] = presupported
    if check_no_images and not has_image:
        result[WARNING_NO_IMAGES] = []
    if check_nested_archive and nested:
        result[WARNING_NESTED] = nested
    if check_no_printable and not has_printable:
        result[WARNING_NO_PRINTABLE] = []
    return {k: result[k] for k in sort_warning_lines(list(result))}


def _detect_warnings_py(
    names: list[str],
    check_presupported: bool = True,
    check_no_images: bool = True,
    check_nested_archive: bool = True,
    check_no_printable: bool = True,
    presupport_patterns: list[str] | None = None,
) -> str:
    """Return a newline-separated string of warnings for an archive.

    Each check is independently enabled so callers can mirror the user's
    configuration flags without applying all checks unconditionally.

    Args:
        names: Archive member names as returned by ``list_contents``.
        check_presupported: Warn when the archive contains an ``.stl`` file
            that is either named as presupported (``body_presupported.stl``,
            ``sup_arm.stl``) or sits in a folder that marks its contents as
            presupported (``Presupported/``, ``PS/``, ``Supported STL/``).
            Only ``.stl`` members qualify, so an image, a readme, or a
            directory entry with "presupported" in its name never raises it.
            See :func:`_is_presupported_member`.
        check_no_images: Warn when the archive contains no recognisable image
            file (``.jpg``, ``.jpeg``, ``.png``, ``.webp``, ``.bmp``, ``.avif``,
            ``.gif``).
        check_nested_archive: Warn when the archive contains another compressed
            archive (``.zip``, ``.rar``, ``.7z``).
        check_no_printable: Warn when the archive contains no printable model
            file (``.stl``, ``.obj``, ``.lys``, ``.chitubox``).
        presupport_patterns: Regex strings (case-insensitive) applied to each
            ``.stl`` member's bare filename for the ``check_presupported`` test.
            ``None`` falls back to ``DEFAULT_PRESUPPORT_PATTERNS``. Invalid
            patterns are skipped.

    Returns:
        Newline-separated warning strings, or ``""`` if no warnings apply.
    """
    warnings: list[str] = []
    has_image          = False
    has_presupported   = False
    has_nested_archive = False
    has_printable      = False

    name_res = compile_presupport_patterns(presupport_patterns) if check_presupported else []

    for name in names:
        # Build the Path once and read both name and suffix from it; constructing
        # Path twice per member is a measurable cost across large archives.
        p = Path(name.replace("\\", "/"))
        suffix = p.suffix.lower()
        if check_no_images and suffix in IMAGE_SUFFIXES:
            has_image = True
        if check_presupported and not has_presupported:
            if _is_presupported_member(p, name_res):
                has_presupported = True
        if check_nested_archive and suffix in ARCHIVE_SUFFIXES:
            has_nested_archive = True
        if check_no_printable and suffix in _PRINTABLE_SUFFIXES:
            has_printable = True

    if check_presupported and has_presupported:
        warnings.append(WARNING_PRESUPPORTED)
        logger.debug("  Warning: models with presupported stl")
    if check_no_images and not has_image:
        warnings.append(WARNING_NO_IMAGES)
        logger.debug("  Warning: archive without images")
    if check_nested_archive and has_nested_archive:
        warnings.append(WARNING_NESTED)
        logger.debug("  Warning: nested archives")
        logger.debug("  Warning: archive with supports")
    if check_no_printable and not has_printable:
        warnings.append(WARNING_NO_PRINTABLE)
        logger.debug("  Warning: archive without printable files")

    if not warnings:
        logger.debug("  No warnings")

    return "\n".join(warnings)


def _detect_scale_py(names: list[str]) -> list[str]:
    """Return a de-duplicated list of scale strings detected in *names*.

    Each entry in *names* is a file path (archive member or the archive path
    itself).  Only the final path component (stem) is scanned.

    Detected patterns:

    - ``<integer>[_ ]?mm``  →  ``"<integer> mm"``   (e.g. ``28mm``, ``32 mm``, ``75_mm``)
    - ``1/<integer>``       →  ``"1/<integer>"``     (e.g. ``1/35``, ``1/72``)
    - ``1-<integer>``       →  ``"1/<integer>"``     (e.g. ``1-35`` → ``"1/35"``)

    Args:
        names: Paths to scan — typically the archive member list prepended with
            the archive file path itself so the archive name is also checked.

    Returns:
        De-duplicated list of scale strings in order of first appearance.
        Empty list when no scale is detected.
    """
    seen:  set[str]  = set()
    found: list[str] = []

    for name in names:
        stem = Path(name.replace("\\", "/")).stem

        for m in _SCALE_MM_RE.finditer(stem):
            scale = f"{m.group(1)} mm"
            if scale not in seen:
                seen.add(scale)
                found.append(scale)
                logger.debug("  Scale detected: %r (from %r)", scale, stem)

        for m in _SCALE_RATIO_RE.finditer(stem):
            scale = f"1/{m.group(1)}"
            if scale not in seen:
                seen.add(scale)
                found.append(scale)
                logger.debug("  Scale detected: %r (from %r)", scale, stem)

    if not found:
        logger.debug("  No scale detected")
    return found


# ── Portable presupport patterns ─────────────────────────────────────────────
# A custom pattern runs in the native rules only when it uses syntax that
# Python's re and fancy-regex read the same way (case-insensitively); the
# Configuration dialog accepts no other.  rust/stl-core/src/rules.rs
# (portable_pattern) implements the same grammar, and
# tests/golden/test_portable_patterns_golden.py compares the two.
#
#   literals      printable ASCII except  . ^ $ * + ? ( ) [ ] { } | \
#   escapes       \d \D \A \Z, and \ before one of  . ^ $ * + ? ( ) [ ] { } | \ - /
#   classes       [...] / [^...] of ASCII characters, ranges and the escapes
#                 above (no nested "[", no "&&", "--", "~~", no leading "]";
#                 a literal "-" only first, last or escaped)
#   groups        (...) (?:...) (?=...) (?!...) (?<=...) (?<!...)
#   quantifiers   * + ? {n} {n,} {n,m}, each optionally lazy (?), after a
#                 literal, ".", an escape, a class or a capturing / plain group
#   anchors       ^ $  ($ means "end, or before a final newline", as in re)

_PORTABLE_META = set(".^$*+?()[]{}|\\")
_PORTABLE_ESCAPABLE = set(".^$*+?()[]{}|\\-/")
_LOOKAROUNDS = ("?:", "?=", "?!", "?<=", "?<!")


def portable_pattern_error(pattern: str) -> "str | None":
    """Why *pattern* is not portable between ``re`` and the native rules, or ``None``."""
    err = _portable_scan(pattern)
    if err is not None:
        return err
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return f"not a valid regular expression ({exc})"
    return None


def _portable_scan(p: str) -> "str | None":
    i, n = 0, len(p)
    depth = 0
    # What the previous token was: None (start, "(" or "|"), "atom" (can be
    # quantified), "anchor"/"look" (cannot), "quant" (may take a lazy "?").
    prev = None
    lookaround_stack: list[bool] = []
    while i < n:
        c = p[i]
        if not (" " <= c <= "~"):
            return f"use only printable ASCII characters (found {c!r} at {i})"
        if c == "\\":
            if i + 1 >= n:
                return "a pattern cannot end with a backslash"
            e = p[i + 1]
            if e in "dD":
                prev = "atom"
            elif e in "AZ":
                prev = "anchor"
            elif e in _PORTABLE_ESCAPABLE:
                prev = "atom"
            else:
                return f"the escape \\{e} is not supported (at {i})"
            i += 2
            continue
        if c == "[":
            j = _portable_class(p, i)
            if isinstance(j, str):
                return j
            i = j
            prev = "atom"
            continue
        if c == "(":
            rest = p[i + 1:]
            if rest.startswith("?"):
                kind = next((k for k in sorted(_LOOKAROUNDS, key=len, reverse=True) if rest.startswith(k)), None)
                if kind is None:
                    return f"only (?:...), (?=...), (?!...), (?<=...) and (?<!...) groups are supported (at {i})"
                lookaround_stack.append(kind != "?:")
                i += 1 + len(kind)
            else:
                lookaround_stack.append(False)
                i += 1
            depth += 1
            prev = None
            continue
        if c == ")":
            if depth == 0:
                return f"unbalanced parenthesis (at {i})"
            depth -= 1
            prev = "look" if lookaround_stack.pop() else "atom"
            i += 1
            continue
        if c == "|":
            prev = None
            i += 1
            continue
        if c in "*+?{":
            if c == "?" and prev == "quant":
                prev = "lazy"
                i += 1
                continue
            if prev != "atom":
                return f"nothing to repeat at {i}"
            if c == "{":
                j = _portable_braces(p, i)
                if isinstance(j, str):
                    return j
                i = j
            else:
                i += 1
            prev = "quant"
            continue
        if c in "^$":
            prev = "anchor"
            i += 1
            continue
        if c == "}" or c == "]":
            return f"escape a literal {c!r} as \\{c} (at {i})"
        prev = "atom"   # literal or "."
        i += 1
    if depth:
        return "unbalanced parenthesis"
    return None


def _portable_braces(p: str, i: int) -> "int | str":
    """End index of a ``{n}``, ``{n,}`` or ``{n,m}`` quantifier starting at *i*."""
    m = re.compile(r"\{([0-9]+)(,([0-9]*))?\}").match(p, i)
    if m is None:
        return f"use {{n}}, {{n,}} or {{n,m}}, or escape a literal '{{' as \\{{ (at {i})"
    lo, hi = int(m.group(1)), m.group(3)
    if hi and int(hi) < lo:
        return f"bad repeat range (at {i})"
    if lo > 1000 or (hi and int(hi) > 1000):
        return f"repeat counts above 1000 are not supported (at {i})"
    return m.end()


def _portable_class(p: str, i: int) -> "int | str":
    """End index (after ``]``) of the character class starting at *i*."""
    j = i + 1
    if j < len(p) and p[j] == "^":
        j += 1
    if j < len(p) and p[j] == "]":
        return f"escape a ']' at the start of a class as \\] (at {j})"
    items = 0
    prev_single: "str | None" = None   # last single character, for ranges
    while j < len(p):
        c = p[j]
        if not (" " <= c <= "~"):
            return f"use only printable ASCII characters (found {c!r} at {j})"
        if c == "]":
            if items == 0:
                return f"empty character class (at {i})"
            return j + 1
        if c == "[":
            return f"escape a '[' inside a class as \\[ (at {j})"
        if p.startswith(("&&", "--", "~~"), j):
            return f"'{p[j:j + 2]}' inside a class is not supported (at {j})"
        if c == "\\":
            if j + 1 >= len(p):
                break
            e = p[j + 1]
            if e in "dD":
                prev_single = None
            elif e in _PORTABLE_ESCAPABLE:
                prev_single = e
            else:
                return f"the escape \\{e} is not supported in a class (at {j})"
            j += 2
            items += 1
            continue
        if c == "-" and prev_single is not None and j + 1 < len(p) and p[j + 1] != "]":
            hi = p[j + 1]
            if hi == "\\":
                if j + 2 >= len(p) or p[j + 2] not in _PORTABLE_ESCAPABLE:
                    return f"a range must end in a single character (at {j})"
                hi, step = p[j + 2], 3
            elif hi == "[":
                return f"escape a '[' inside a class as \\[ (at {j + 1})"
            else:
                step = 2
            if not (" " <= hi <= "~"):
                return f"use only printable ASCII characters (found {hi!r} at {j + 1})"
            if hi < prev_single:
                return f"bad character range {prev_single}-{hi} (at {j})"
            j += step
            prev_single = None
            items += 1
            continue
        if c == "-" and items and not (j + 1 < len(p) and p[j + 1] == "]"):
            return f"escape a '-' in the middle of a class as \\- (at {j})"
        prev_single = c
        items += 1
        j += 1
    return f"unterminated character class (at {i})"


# ── Native versions ──────────────────────────────────────────────────────────
# rust/stl-core/src/rules.rs ports the functions above; tests/golden compares
# them. The native call raises for input it does not handle exactly (a name
# with lone surrogates, for one), and the Python implementation then runs.
#
# Custom presupport patterns run natively only when every one is portable
# (see portable_pattern_error): fancy-regex and Python's re disagree on some
# syntax without either raising ("[[:alpha:]]" is a POSIX class in one and a
# character set in the other; "(?<name>x)" and "\x{41}" are valid only
# natively).  Other lists use Python.  The built-in defaults are golden-tested.

_NATIVE_ERRORS = (ValueError, TypeError, UnicodeError, AttributeError)


def _native():
    from app import native
    return native.module()


def detect_presupport(names: list[str]) -> str:
    """Presupport classification of an archive's members; see :func:`_detect_presupport_py`."""
    mod = _native()
    if mod is not None:
        try:
            return mod.detect_presupport(list(names))
        except _NATIVE_ERRORS:
            pass
    return _detect_presupport_py(names)


def _native_patterns_ok(check_presupported: bool, patterns) -> bool:
    """True when the native rules give Python's result for these patterns:
    the defaults, or custom patterns that are all portable."""
    return (not check_presupported or patterns is None
            or list(patterns) == DEFAULT_PRESUPPORT_PATTERNS
            or all(portable_pattern_error(p) is None for p in patterns))


def explain_warnings(
    names: list[str],
    check_presupported: bool = True,
    check_no_images: bool = True,
    check_nested_archive: bool = True,
    check_no_printable: bool = True,
    presupport_patterns: "list[str] | None" = None,
) -> "dict[str, list[str]]":
    """``{warning: [member names that triggered it]}``; see :func:`_explain_warnings_py`."""
    flags = (check_presupported, check_no_images, check_nested_archive, check_no_printable)
    mod = _native()
    if mod is not None and _native_patterns_ok(check_presupported, presupport_patterns):
        try:
            return dict(mod.explain_warnings(
                list(names), *flags,
                list(presupport_patterns) if presupport_patterns is not None else None))
        except _NATIVE_ERRORS:
            pass
    return _explain_warnings_py(names, *flags, presupport_patterns=presupport_patterns)


def detect_warnings(
    names: list[str],
    check_presupported: bool = True,
    check_no_images: bool = True,
    check_nested_archive: bool = True,
    check_no_printable: bool = True,
    presupport_patterns: "list[str] | None" = None,
) -> str:
    """Newline-separated warnings for an archive; see :func:`_detect_warnings_py`."""
    flags = (check_presupported, check_no_images, check_nested_archive, check_no_printable)
    mod = _native()
    if mod is not None and _native_patterns_ok(check_presupported, presupport_patterns):
        try:
            return mod.detect_warnings(
                list(names), *flags,
                list(presupport_patterns) if presupport_patterns is not None else None)
        except _NATIVE_ERRORS:
            pass
    return _detect_warnings_py(names, *flags, presupport_patterns=presupport_patterns)


def detect_scale(names: list[str]) -> list[str]:
    """Scales (``"28 mm"``, ``"1/35"``) found in the names; see :func:`_detect_scale_py`."""
    mod = _native()
    if mod is not None:
        try:
            return mod.detect_scale(list(names))
        except _NATIVE_ERRORS:
            pass
    return _detect_scale_py(names)
