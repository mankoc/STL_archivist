"""
Shared path-pattern utilities — used by the mass editor and the crawler.

Pattern syntax:
  {token}  → named capture  (token names: source, creator/author, title/name,
                              date/month, url, sculptor, collection/category,
                              subcollection/subcategory, sequence)
  *        → ignored segment

FK-backed tokens (sculptor, collection, subcollection, sequence) are stored under
"virtual" keys in the extracted dict and require apply_extracted_to_model()
to resolve them into the correct DB objects.
"""
import re
from functools import lru_cache


def parse_release_date(s: str) -> tuple[int | None, int | None]:
    """Parse a date string like 'YYYY-MM', 'YYYY_MM', or 'YYYY' into (year, month).

    Returns (None, None) for unrecognised input.
    """
    if not s:
        return None, None
    m = re.match(r'^(\d{4})[-_](\d{1,2})$', s.strip())
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r'^(\d{4})$', s.strip())
    if m:
        return int(m.group(1)), None
    return None, None


def format_release_date(year: int | None, month: int | None) -> str:
    """Format release_year / release_month integers into a display string.

    Returns '' when year is None.
    """
    if year is None:
        return ""
    if month is not None:
        return f"{year}-{month:02d}"
    return str(year)

# Maps lowercase token name → field key used in extracted dicts.
# Plain string fields map directly to STLModel attributes.
# FK / special fields use a "virtual" key (e.g. "sculptor_name") that
# apply_extracted_to_model() knows how to handle.
TOKEN_MAP = {
    "source":         "source",
    "creator":        "creator",
    "author":         "creator",        # backward-compat alias
    "title":          "title",
    "name":           "title",
    "date":           "release_date",
    "month":          "release_date",
    "url":            "url",
    "sculptor":       "sculptor_name",
    "collection":     "collection_name",
    "category":       "collection_name",  # backward-compat alias
    "subcollection":  "subcollection_name",
    "subcategory":    "subcollection_name",  # backward-compat alias
    "sequence":       "sequence",
    "release":        "release_name",
}

# Simple string attributes that can be set with setattr directly
_SIMPLE_ATTRS = {"source", "creator", "title", "url"}

# Human-readable label for each field key (for preview lines)
ATTR_LABEL = {
    "source":              "source",
    "creator":             "creator",
    "title":               "title",
    "release_date":        "date",
    "url":                 "url",
    "sculptor_name":       "sculptor",
    "collection_name":     "collection",
    "subcollection_name":  "subcollection",
    "sequence":            "sequence",
    "release_name":        "release",
}

PATTERN_HELP = (
    "<b>Pattern syntax</b><br>"
    "Use <code>{token}</code> to capture a path segment, <code>*</code> to skip one.<br><br>"
    "<table cellspacing='0' cellpadding='2'>"
    "<tr><td><code>{source}</code></td><td>&nbsp;–&nbsp;</td><td>distribution / shop name</td></tr>"
    "<tr><td><code>{creator}</code> / <code>{author}</code></td><td>&nbsp;–&nbsp;</td><td>creator / designer name</td></tr>"
    "<tr><td><code>{title}</code> / <code>{name}</code></td><td>&nbsp;–&nbsp;</td><td>model title</td></tr>"
    "<tr><td><code>{date}</code> / <code>{month}</code></td><td>&nbsp;–&nbsp;</td><td>release date</td></tr>"
    "<tr><td><code>{sculptor}</code></td><td>&nbsp;–&nbsp;</td><td>sculptor (looked up or created)</td></tr>"
    "<tr><td><code>{collection}</code> / <code>{category}</code></td><td>&nbsp;–&nbsp;</td><td>collection (looked up or created)</td></tr>"
    "<tr><td><code>{subcollection}</code> / <code>{subcategory}</code></td><td>&nbsp;–&nbsp;</td><td>subcollection / variant</td></tr>"
    "<tr><td><code>{sequence}</code></td><td>&nbsp;–&nbsp;</td><td>numbering within a series</td></tr>"
    "<tr><td><code>{release}</code></td><td>&nbsp;–&nbsp;</td><td>release / bundle name</td></tr>"
    "<tr><td><code>{url}</code></td><td>&nbsp;–&nbsp;</td><td>source URL</td></tr>"
    "<tr><td><code>*</code></td><td>&nbsp;–&nbsp;</td><td>ignored segment (any value)</td></tr>"
    "</table><br>"
    "<b>Example:</b> <code>/{source}/{creator}/*/{name}</code>"
)


@lru_cache(maxsize=64)
def pattern_to_regex(pattern: str) -> re.Pattern | None:
    """Compile *pattern* to a regex.

    ``{token}`` → named capture group ``(?P<token>[^/]+)``
    ``*``        → anonymous match  ``[^/]+``
    Duplicate token names get a numeric suffix so the regex stays valid.
    Returns None when the pattern contains no tokens or wildcards.
    """
    has_placeholder = bool(re.search(r"\{(\w+)\}|\*", pattern))
    if not has_placeholder:
        return None

    seen: dict[str, int] = {}
    parts = re.split(r"(\{\w+\}|\*)", pattern)
    # Identify the last token placeholder so only it gets greedy matching;
    # earlier tokens use non-greedy to enforce left-to-right consumption.
    placeholder_indices = [
        i for i, p in enumerate(parts)
        if p == "*" or (p.startswith("{") and p.endswith("}"))
    ]
    last_ph = placeholder_indices[-1] if placeholder_indices else -1

    regex_parts: list[str] = []
    for i, part in enumerate(parts):
        if part == "*":
            regex_parts.append("[^/]+")
        elif part.startswith("{") and part.endswith("}"):
            token = part[1:-1]
            count = seen.get(token, 0)
            seen[token] = count + 1
            group = token if count == 0 else f"{token}_{count}"
            quantifier = "[^/]+" if i == last_ph else "[^/]+?"
            regex_parts.append(f"(?P<{group}>{quantifier})")
        else:
            regex_parts.append(re.escape(part))

    try:
        return re.compile("".join(regex_parts))
    except re.error:
        return None


def _result_from_match(m: re.Match) -> dict[str, str]:
    """Convert a regex match to a field-value dict, stripping extensions from title."""
    import os
    result: dict[str, str] = {}
    for group, value in m.groupdict().items():
        base_token = re.sub(r"_\d+$", "", group)
        attr = TOKEN_MAP.get(base_token.lower())
        if attr and value:
            if attr == "title":
                # Strip archive extension if the captured value looks like a filename
                root, _ = os.path.splitext(value)
                if root:
                    value = root
            result[attr] = value
    return result


def parse_path(pattern: str, path: str) -> dict[str, str]:
    """Extract field values from *path* using *pattern*.

    Tries two passes so that both flat and nested structures are handled:

    Pass 1 — full archive path.  If the match reaches the very end of the
             string, ``{name}`` captured the archive filename → good match.

    Pass 2 — parent directory only.  Used when Pass 1 either failed or
             stopped short (``{name}`` captured a subdirectory, not the
             archive).  The archive stem is injected as ``title`` if
             ``{name}``/``{title}`` is not otherwise captured.

    Returns a dict mapping field keys → extracted strings.
    Returns an empty dict if neither pass matches.
    """
    import os
    rx = pattern_to_regex(pattern)
    if rx is None:
        return {}

    # Normalise to forward slashes for consistent matching
    norm = path.replace("\\", "/")
    parent = norm.rsplit("/", 1)[0] if "/" in norm else norm
    archive_stem, _ = os.path.splitext(norm.rsplit("/", 1)[-1])

    archive_filename = norm.rsplit("/", 1)[-1] if "/" in norm else norm

    # Pass 1: full path — accept only if the match reaches the string end
    # (meaning {name} captured the actual archive filename, not a directory)
    m = rx.search(norm)
    if m and m.end() == len(norm):
        return _result_from_match(m)

    # Pass 1b: filename only — for patterns without path separators, match
    # against just the last segment so a directory with the same separator
    # string (e.g. "Vengeance - 3dbyJT") doesn't shadow the real filename.
    if "/" not in pattern:
        m_fn = rx.search(archive_filename)
        if m_fn and m_fn.end() == len(archive_filename):
            return _result_from_match(m_fn)

    # Pass 2: parent directory path
    if parent and parent != norm:
        m2 = rx.search(parent)
        if m2:
            result = _result_from_match(m2)
            # Fill title from archive stem if the pattern didn't capture it
            if "title" not in result and archive_stem:
                result["title"] = archive_stem
            return result

    # Pass 1 fallback: match existed but didn't reach the end
    # (e.g. {name} grabbed a directory) — still return what we have
    if m:
        return _result_from_match(m)

    return {}


def render_path_pattern(pattern: str, model) -> str:
    """Expand *pattern* using *model* attributes.

    Replaces ``{token}`` placeholders with the corresponding value from the
    STLModel instance.  Unknown or empty tokens are replaced with ``_unknown``.
    The result is NOT joined with a base directory — callers do that.

    Only the *filename* part (last segment) keeps the original archive
    extension so the resulting path remains a valid archive path.
    """
    import os
    import re

    # Build a lookup dict from model attributes
    def _safe(v) -> str:
        if v is None:
            return "_unknown"
        s = str(v).strip()
        return s if s else "_unknown"

    values: dict[str, str] = {
        "source":        _safe(model.source),
        "creator":       _safe(model.creator),
        "author":        _safe(model.creator),   # backward-compat alias
        "title":         _safe(model.title),
        "name":          _safe(model.title),
        "date":          _safe(format_release_date(model.release_year, model.release_month)),
        "month":         _safe(format_release_date(model.release_year, model.release_month)),
        "url":           _safe(model.url),
        "sculptor":      _safe(model.sculptor.name    if model.sculptor    else None),
        "collection":    _safe(model.collection.name  if model.collection  else None),
        "category":      _safe(model.collection.name  if model.collection  else None),  # backward-compat
        "subcollection": _safe(model.subcollection.name if model.subcollection else None),
        "subcategory":   _safe(model.subcollection.name if model.subcollection else None),  # backward-compat
        "sequence":      _safe(model.sequence),
        "release":       _safe(model.release.name    if model.release     else None),
    }

    # Replace each {token} with its value, sanitising directory-separator chars
    def _sub(m: re.Match) -> str:
        token = m.group(1).lower()
        raw = values.get(token, "_unknown")
        # Strip characters unsafe in path components
        safe = re.sub(r'[\\/:*?"<>|]', "_", raw)
        return safe

    rendered = re.sub(r"\{(\w+)\}", _sub, pattern)
    return rendered


def apply_extracted_to_model(session, model, extracted: dict[str, str],
                             overwrite: bool = True) -> None:
    """Write *extracted* field values onto *model* inside *session*.

    Simple string fields are set directly.  FK-backed fields (sculptor_name,
    model_type_name, subtype_name, sequence) trigger a DB lookup / create.

    When *overwrite* is False, only fields that are currently None / empty are
    filled in (used by the crawler to let archive metadata take precedence).
    """
    from app.database.models import Sculptor, Collection, Subcollection, Release  # local to avoid circular imports

    def _should_set(current_value) -> bool:
        if overwrite:
            return True
        return not current_value

    for attr, value in extracted.items():
        if not value:
            continue

        if attr in _SIMPLE_ATTRS:
            if _should_set(getattr(model, attr, None)):
                setattr(model, attr, value)

        elif attr == "release_date":
            if _should_set(model.release_year):
                year, month = parse_release_date(value)
                if year is not None:
                    model.release_year = year
                    model.release_month = month

        elif attr == "sculptor_name":
            if _should_set(model.sculptor_id):
                sc = session.query(Sculptor).filter_by(name=value).first()
                if sc is None:
                    sc = Sculptor(name=value)
                    session.add(sc)
                    session.flush()
                model.sculptor = sc

        elif attr == "collection_name":
            if _should_set(model.collection_id):
                col = session.query(Collection).filter_by(name=value).first()
                if col is None:
                    col = Collection(name=value)
                    session.add(col)
                    session.flush()
                model.collection = col

        elif attr == "subcollection_name":
            if _should_set(model.subcollection_id):
                # Prefer a subcollection that matches the model's current collection
                q = session.query(Subcollection).filter_by(name=value)
                if model.collection_id:
                    q = q.filter_by(collection_id=model.collection_id)
                sc = q.first()
                if sc is None and model.collection_id:
                    sc = Subcollection(name=value, collection_id=model.collection_id)
                    session.add(sc)
                    session.flush()
                if sc:
                    model.subcollection = sc

        elif attr == "sequence":
            if _should_set(model.sequence):
                try:
                    model.sequence = int(value)
                except ValueError:
                    pass

        elif attr == "release_name":
            if _should_set(model.release_id):
                rel = session.query(Release).filter_by(name=value).first()
                if rel is None:
                    rel = Release(name=value)
                    session.add(rel)
                    session.flush()
                model.release = rel
