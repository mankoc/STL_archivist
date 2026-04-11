"""
Shared path-pattern utilities — used by the mass editor and the crawler.

Pattern syntax:
  {token}  → named capture  (token names: type, author, title/name, date/month,
                              url, sculptor, model_type, subtype, sequence)
  *        → ignored segment

FK-backed tokens (sculptor, model_type, subtype, sequence) are stored under
"virtual" keys in the extracted dict and require apply_extracted_to_model()
to resolve them into the correct DB objects.
"""
import re

# Maps lowercase token name → field key used in extracted dicts.
# Plain string fields map directly to STLModel attributes.
# FK / special fields use a "virtual" key (e.g. "sculptor_name") that
# apply_extracted_to_model() knows how to handle.
TOKEN_MAP = {
    "type":       "source",
    "author":     "author",
    "title":      "title",
    "name":       "title",
    "date":       "release_date",
    "month":      "release_date",
    "url":        "url",
    "sculptor":   "sculptor_name",
    "model_type": "model_type_name",
    "subtype":    "subtype_name",
    "sequence":   "sequence",
    "release":    "release_name",
}

# Simple string attributes that can be set with setattr directly
_SIMPLE_ATTRS = {"source", "author", "title", "release_date", "url"}

# Human-readable label for each field key (for preview lines)
ATTR_LABEL = {
    "source":          "source",
    "author":          "author",
    "title":           "title",
    "release_date":    "date",
    "url":             "url",
    "sculptor_name":   "sculptor",
    "model_type_name": "type",
    "subtype_name":    "subtype",
    "sequence":        "sequence",
    "release_name":    "release",
}

PATTERN_HELP = (
    "<b>Pattern syntax:</b> use <code>{token}</code> for captured segments, "
    "<code>*</code> for ignored segments.<br>"
    "Supported tokens:&nbsp; "
    "<code>{type}</code>, <code>{author}</code>, "
    "<code>{title}</code>&nbsp;/&nbsp;<code>{name}</code>, "
    "<code>{date}</code>&nbsp;/&nbsp;<code>{month}</code>, "
    "<code>{url}</code>, <code>{sculptor}</code>, "
    "<code>{model_type}</code>, <code>{subtype}</code>, "
    "<code>{sequence}</code>, <code>{release}</code><br>"
    "<b>Example:</b>&nbsp; <code>/mnt/f/{type}/{author}/*/{name}</code>"
)


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
    regex_parts: list[str] = []
    for part in parts:
        if part == "*":
            regex_parts.append("[^/]+")
        elif part.startswith("{") and part.endswith("}"):
            token = part[1:-1]
            count = seen.get(token, 0)
            seen[token] = count + 1
            group = token if count == 0 else f"{token}_{count}"
            regex_parts.append(f"(?P<{group}>[^/]+)")
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

    # Pass 1: full path — accept only if the match reaches the string end
    # (meaning {name} captured the actual archive filename, not a directory)
    m = rx.search(norm)
    if m and m.end() == len(norm):
        return _result_from_match(m)

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


def apply_extracted_to_model(session, model, extracted: dict[str, str],
                             overwrite: bool = True) -> None:
    """Write *extracted* field values onto *model* inside *session*.

    Simple string fields are set directly.  FK-backed fields (sculptor_name,
    model_type_name, subtype_name, sequence) trigger a DB lookup / create.

    When *overwrite* is False, only fields that are currently None / empty are
    filled in (used by the crawler to let archive metadata take precedence).
    """
    from app.database.models import Sculptor, ModelType, Subtype, Release  # local to avoid circular imports

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

        elif attr == "sculptor_name":
            if _should_set(model.sculptor_id):
                sc = session.query(Sculptor).filter_by(name=value).first()
                if sc is None:
                    sc = Sculptor(name=value)
                    session.add(sc)
                    session.flush()
                model.sculptor = sc

        elif attr == "model_type_name":
            if _should_set(model.type_id):
                mt = session.query(ModelType).filter_by(name=value).first()
                if mt is None:
                    mt = ModelType(name=value)
                    session.add(mt)
                    session.flush()
                model.type = mt

        elif attr == "subtype_name":
            if _should_set(model.subtype_id):
                # Prefer a subtype that matches the model's current type
                q = session.query(Subtype).filter_by(name=value)
                if model.type_id:
                    q = q.filter_by(model_type_id=model.type_id)
                st = q.first()
                if st is None and model.type_id:
                    st = Subtype(name=value, model_type_id=model.type_id)
                    session.add(st)
                    session.flush()
                if st:
                    model.subtype = st

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
