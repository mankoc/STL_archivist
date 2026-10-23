"""Write docs/api.md (the HTTP API reference) from web/openapi.json.

Run after `npm run gen:api` (which refreshes web/openapi.json from the server):

    python tools/api_doc.py
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "web" / "openapi.json"
TARGET = ROOT / "docs" / "api.md"

# Sections in reading order, with their headings.
TAGS = [
    ("auth", "Sign-in and sessions"),
    ("users", "Users and preferences"),
    ("system", "System"),
    ("library", "Browsing the library"),
    ("editing", "Editing models"),
    ("lookups", "Lookups (sculptors, collections, tags…)"),
    ("thumbnails", "Thumbnails and images"),
    ("files", "Files and folders"),
    ("libraries", "Libraries and their folders"),
    ("jobs", "Jobs and events"),
    ("maintenance", "Maintenance"),
    ("backups", "Backups"),
    ("search", "Search"),
    ("statistics", "Statistics"),
    ("settings", "Library settings"),
    ("ai", "AI tagger"),
    ("workers", "Compute workers"),
    ("components", "Optional components"),
]
METHODS = ["get", "post", "put", "patch", "delete"]


def one_line(text):
    return re.sub(r"\s+", " ", text or "").strip()


def cell(text):
    # Rust doc links (`[`ServerEvent`]`) read as plain code here.
    text = re.sub(r"\[(`[^`]+`)\](?!\()", r"\1", one_line(text))
    return text.replace("|", "\\|")


def ok(code):
    """A status that is no error (2xx, or 101 for the WebSockets)."""
    return code.startswith(("1", "2"))


def ref_name(ref):
    return ref.rsplit("/", 1)[-1]


def type_of(schema):
    """A schema as a short type: `Name` (linked), `T[]`, `T | null`, `string`…"""
    if not schema:
        return ""
    if "$ref" in schema:
        name = ref_name(schema["$ref"])
        return f"[{name}](#{name.lower()})"
    for key in ("oneOf", "anyOf", "allOf"):
        if key in schema:
            return " \\| ".join(type_of(s) for s in schema[key])
    t = schema.get("type")
    if isinstance(t, list):
        rest = [x for x in t if x != "null"]
        base = type_of({**schema, "type": rest[0] if len(rest) == 1 else rest}) if rest else "null"
        return f"{base} \\| null" if "null" in t else base
    if t == "array":
        return f"{type_of(schema.get('items', {}))}[]"
    if "enum" in schema:
        return " \\| ".join(f"`{json.dumps(v)}`" for v in schema["enum"])
    if t == "object" and "additionalProperties" in schema:
        inner = schema["additionalProperties"]
        return f"map of {type_of(inner) if isinstance(inner, dict) else 'any'}"
    if t:
        fmt = schema.get("format")
        return f"{t} ({fmt})" if fmt and fmt not in ("int64", "int32", "double") else t
    return "any"


def body_of(content):
    """The JSON (or other) body of a request or response, as a short type."""
    if not content:
        return ""
    for mime, media in content.items():
        if mime == "application/json":
            return type_of(media.get("schema", {}))
        return f"`{mime}`"
    return ""


def request_of(op):
    parts = []
    for p in op.get("parameters", []):
        where = p.get("in")
        name = f"`{p['name']}`" + ("" if p.get("required") else "?")
        parts.append(f"{where} {name}: {type_of(p.get('schema', {}))}")
    rb = op.get("requestBody")
    if rb:
        parts.append("body: " + body_of(rb.get("content")))
    return "<br>".join(parts)


def response_of(op):
    out = []
    for code, r in sorted(op.get("responses", {}).items()):
        if not ok(code):
            continue
        body = body_of(r.get("content"))
        out.append(f"{code}" + (f" {body}" if body else ""))
    return "<br>".join(out)


def errors_of(op):
    return ", ".join(c for c in sorted(op.get("responses", {})) if not ok(c))


def anchor(title):
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")


def main():
    doc = json.loads(SOURCE.read_text(encoding="utf-8"))
    info = doc["info"]
    ops = {}
    for path, methods in doc["paths"].items():
        for method in METHODS:
            if method in methods:
                op = methods[method]
                tag = (op.get("tags") or ["other"])[0]
                ops.setdefault(tag, []).append((path, method, op))
    tags = TAGS + [(t, t.title()) for t in sorted(ops) if t not in dict(TAGS)]
    total = sum(len(v) for v in ops.values())

    out = [
        f"# {info['title']}",
        "",
        f"Version {info['version']}. Generated from `web/openapi.json` by `tools/api_doc.py`; the",
        "running server serves the same document at `GET /api/openapi.json`. Do not edit by hand:",
        "run `npm run gen:api` (in `web/`) and then `python tools/api_doc.py` after changing routes.",
        "",
        "## Conventions",
        "",
        # The description's first sentence is the title again.
        f"- {one_line(info.get('description')).split('. ', 1)[-1]}",
        "- Requests and responses are JSON. Request bodies refuse unknown fields (422).",
        "- **Roles:** every signed-in user may read (viewer). Routes marked *editors* need the",
        "  editor role, *administrators* the admin role; otherwise the answer is 403.",
        "- **Errors** answer with an [ErrorBody](#errorbody) (`{\"error\": \"…\"}`) and a status:",
        "  400 invalid request, 401 not signed in, 403 role too low, 404 not found, 409 conflict,",
        "  422 a body that does not parse.",
        "- Long work (crawls, maintenance, backups, AI batches) runs as a [job](#jobs-and-events):",
        "  `POST /api/jobs` answers 202 with the job; follow it on `GET /api/events` (WebSocket)",
        "  or `GET /api/jobs/{id}`.",
        "- Writes that change the library are announced on `/api/events` as `library_changed`.",
        "",
        f"## Endpoints ({total})",
        "",
    ]
    for tag, title in tags:
        if tag in ops:
            out.append(f"- [{title}](#{anchor(title)}) ({len(ops[tag])})")
    out.append("- [Types](#types)")
    out.append("")

    for tag, title in tags:
        if tag not in ops:
            continue
        out += [f"### {title}", "", "| Endpoint | Description | Request | Response | Errors |", "|---|---|---|---|---|"]
        for path, method, op in ops[tag]:
            desc = cell(" ".join(x for x in (op.get("summary"), op.get("description")) if x))
            out.append(f"| `{method.upper()} {path}` | {desc} | {request_of(op)} | {response_of(op)} | {errors_of(op)} |")
        out.append("")

    out += ["## Types", "", "The request and response bodies named above.", ""]
    for name, schema in sorted(doc.get("components", {}).get("schemas", {}).items()):
        out += [f"### {name}", ""]
        if schema.get("description"):
            out += [one_line(schema["description"]), ""]
        props = schema.get("properties")
        if props:
            required = set(schema.get("required", []))
            out += ["| Field | Type | Description |", "|---|---|---|"]
            for field, s in props.items():
                opt = "" if field in required else " (optional)"
                out.append(f"| `{field}`{opt} | {type_of(s)} | {cell(s.get('description', ''))} |")
            out.append("")
        elif "oneOf" in schema:
            out += ["One of:", ""]
            for variant in schema["oneOf"]:
                vprops = variant.get("properties", {})
                kind = next((f"`{k}: {json.dumps(v['enum'][0])}`" for k, v in vprops.items() if "enum" in v and len(v["enum"]) == 1), "")
                fields = ", ".join(f"`{k}`" for k in vprops if not ("enum" in vprops[k] and len(vprops[k]["enum"]) == 1))
                label = type_of(variant) if "$ref" in variant else kind or "variant"
                desc = cell(variant.get("description", ""))
                out.append(f"- {label}" + (f" — {fields}" if fields else "") + (f": {desc}" if desc else ""))
            out.append("")
        else:
            out += [f"Type: {type_of(schema)}", ""]
    TARGET.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8", newline="\n")
    print(f"{TARGET.relative_to(ROOT)}: {total} endpoints")


if __name__ == "__main__":
    main()
