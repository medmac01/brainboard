"""Shared storage + operations for brainboard.

Boards live under BRAINBOARD_HOME (default ./data), one JSON file per project:
projects/<slug>.json. Each project file is a complete board (meta, items,
thoughts, activity). Every writer (web server, CLI, any agent) goes through
apply_op(), which takes that project's file lock, re-reads the file, applies
one operation, bumps `rev`, logs activity and writes atomically.

Older single-board installs (BRAINBOARD_DATA=…/board.json) are migrated on
first start: the board becomes the first project and the old file is renamed
to board.json.migrated.
"""
import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
_LEGACY_ENV = os.path.expanduser(os.environ.get("BRAINBOARD_DATA", ""))
HOME = os.path.abspath(os.path.expanduser(
    os.environ.get("BRAINBOARD_HOME")
    or (os.path.dirname(_LEGACY_ENV) if _LEGACY_ENV else os.path.join(HERE, "..", "data"))))
PROJECTS = os.path.join(HOME, "projects")
TRASH = os.path.join(HOME, "trash")
LEGACY = os.path.abspath(_LEGACY_ENV) if _LEGACY_ENV else os.path.join(HOME, "board.json")

DEFAULT_CATEGORIES = ["Inbox", "Build", "Research", "Writing", "Admin"]
DEFAULT_PEOPLE = ["me", "claude", "codex"]
COLORS = ["#5b5bd6", "#2f6fde", "#0e8f87", "#2f8a57", "#b7791f", "#d0602b", "#d1456a", "#8b4fd6"]

STATUSES = ["todo", "doing", "decide", "blocked", "done", "skip"]
PRIORITIES = ["P1", "P2", "P3"]
ITEM_FIELDS = {
    "title", "category", "priority", "kind", "status", "progress", "owner",
    "location", "anchor", "issue", "fix", "sources", "tex", "tags", "pinned",
}
EXP_STATUSES = ["planned", "running", "done", "failed", "dropped"]
OUTCOMES = ["", "worked", "inconclusive", "didnt"]
EXP_FIELDS = {"title", "status", "outcome", "owner", "problem", "setup", "location", "results", "related", "tags"}
META_FIELDS = {"title", "description", "icon", "color", "categories", "people", "archived"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
ACTIVITY_CAP = 500


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-") or "project"


def path(slug):
    if not SLUG_RE.match(slug or ""):
        raise ValueError(f"bad project slug: {slug!r}")
    return os.path.join(PROJECTS, slug + ".json")


def exists(slug):
    return os.path.exists(path(slug))


def slugs():
    if not os.path.isdir(PROJECTS):
        return []
    return sorted(f[:-5] for f in os.listdir(PROJECTS) if f.endswith(".json") and SLUG_RE.match(f[:-5]))


def empty_board(title="My project", **meta):
    m = {"title": title, "description": "", "icon": "📌", "color": COLORS[0], "rev": 0,
         "created": now(), "updated": now(), "updated_by": "brainboard", "archived": False,
         "sources": {}, "categories": list(DEFAULT_CATEGORIES), "people": list(DEFAULT_PEOPLE)}
    m.update(meta)
    return {"meta": m, "items": [], "experiments": [], "thoughts": [], "activity": []}


def ensure_home():
    os.makedirs(PROJECTS, exist_ok=True)
    if os.path.isfile(LEGACY) and not slugs():
        _migrate_legacy()


def _migrate_legacy():
    with open(LEGACY, encoding="utf-8") as fh:
        data = json.load(fh)
    meta = data.setdefault("meta", {})
    defaults = empty_board(meta.get("title") or "My board")["meta"]
    for k, v in defaults.items():
        meta.setdefault(k, v)
    slug = slugify(meta["title"])
    _log(data, "brainboard", "board migrated into project “%s”" % meta["title"])
    save(slug, data)
    os.replace(LEGACY, LEGACY + ".migrated")


@contextmanager
def locked(slug=None):
    os.makedirs(PROJECTS, exist_ok=True)
    with open(os.path.join(PROJECTS, f".{slug or '_index'}.lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load(slug):
    p = path(slug)
    if not os.path.exists(p):
        raise KeyError(f"no such project: {slug}")
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    data["meta"]["slug"] = slug
    return data


def save(slug, data):
    os.makedirs(PROJECTS, exist_ok=True)
    data["meta"].pop("slug", None)
    fd, tmp = tempfile.mkstemp(dir=PROJECTS, prefix=".tmp.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path(slug))
    data["meta"]["slug"] = slug


def _find(seq, id_):
    for x in seq:
        if x["id"] == id_:
            return x
    raise KeyError(f"no such id: {id_}")


def _next_id(seq, prefix):
    n = 0
    for x in seq:
        if x["id"].startswith(prefix) and x["id"][len(prefix):].isdigit():
            n = max(n, int(x["id"][len(prefix):]))
    return f"{prefix}{n + 1:02d}"


def _log(data, who, text, item=None):
    data.setdefault("activity", []).insert(0, {"at": now(), "who": who, "text": text, "item": item})
    del data["activity"][ACTIVITY_CAP:]


def _clean_fields(fields):
    bad = set(fields) - ITEM_FIELDS
    if bad:
        raise ValueError(f"unknown field(s): {', '.join(sorted(bad))}")
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if "priority" in fields and fields["priority"] not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    if "progress" in fields:
        fields["progress"] = max(0, min(100, int(fields["progress"])))
    return fields


def _clean_exp(fields):
    bad = set(fields) - EXP_FIELDS
    if bad:
        raise ValueError(f"unknown experiment field(s): {', '.join(sorted(bad))}")
    if "status" in fields and fields["status"] not in EXP_STATUSES:
        raise ValueError(f"experiment status must be one of {EXP_STATUSES}")
    if "outcome" in fields and fields["outcome"] not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES[1:]} (or empty)")
    for k in ("related", "tags"):
        if k in fields:
            fields[k] = _as_list(fields[k])
    return fields


def _exp_times(fields, ex):
    """Stamp started/ended when an experiment's status moves."""
    st = fields.get("status")
    if st == "running" and not ex.get("started"):
        fields["started"] = now()
    if st in ("done", "failed", "dropped") and ex.get("status") not in ("done", "failed", "dropped"):
        fields["ended"] = now()
    if st in ("planned", "running"):
        fields["ended"] = ""


def _locate(data, id_):
    """Find an item or an experiment by id. Returns (record, is_experiment)."""
    for x in data["items"]:
        if x["id"] == id_:
            return x, False
    for x in data.setdefault("experiments", []):
        if x["id"] == id_:
            return x, True
    raise KeyError(f"no such id: {id_}")


def _as_list(v):
    if isinstance(v, str):
        v = re.split(r"[,\n]", v)
    return [str(x).strip() for x in v if str(x).strip()]


def _clean_meta(fields):
    bad = set(fields) - META_FIELDS
    if bad:
        raise ValueError(f"unknown project field(s): {', '.join(sorted(bad))}")
    f = dict(fields)
    if "title" in f:
        f["title"] = str(f["title"]).strip()[:120]
        if not f["title"]:
            raise ValueError("title can't be empty")
    if "description" in f:
        f["description"] = str(f["description"]).strip()[:500]
    if "icon" in f:
        f["icon"] = str(f["icon"]).strip()[:8] or "📌"
    if "color" in f and not re.match(r"^#[0-9a-fA-F]{6}$", str(f["color"])):
        raise ValueError("color must look like #5b5bd6")
    for k in ("categories", "people"):
        if k in f:
            f[k] = _as_list(f[k])
            if not f[k]:
                raise ValueError(f"{k} can't be empty")
    if "archived" in f:
        f["archived"] = f["archived"] in (True, 1, "1", "true", "yes")
    return f


def _describe(fields):
    parts = []
    for k, v in fields.items():
        if k in ("issue", "fix", "anchor", "description") or isinstance(v, list) or (isinstance(v, str) and len(v) > 40):
            parts.append(f"edited {k}")
        else:
            parts.append(f"{k} → {v}")
    return ", ".join(parts)


def apply_op(slug, op, who="me"):
    """Apply one operation dict to a project. Returns the project's new full state."""
    kind = op.get("op")
    with locked(slug):
        data = load(slug)
        items, thoughts = data["items"], data.setdefault("thoughts", [])
        exps = data.setdefault("experiments", [])
        if kind == "update" and _locate(data, op["id"])[1]:
            ex = _find(exps, op["id"])
            fields = _clean_exp(dict(op["fields"]))
            _exp_times(fields, ex)
            changed = {k: v for k, v in fields.items() if ex.get(k) != v}
            if not changed:
                return data
            ex.update(changed)
            ex["updated"] = now()
            _log(data, who, _describe({k: v for k, v in changed.items() if k not in ("started", "ended")}), ex["id"])
        elif kind == "update":
            it = _find(items, op["id"])
            fields = _clean_fields(dict(op["fields"]))
            if fields.get("status") == "done":
                fields.setdefault("progress", 100)
            if fields.get("status") == "todo" and it.get("status") == "done":
                fields.setdefault("progress", 0)
            if fields.get("progress") == 100 and "status" not in fields and it["status"] not in ("done", "skip"):
                fields["status"] = "done"
            changed = {k: v for k, v in fields.items() if it.get(k) != v}
            if not changed:
                return data
            it.update(changed)
            it["updated"] = now()
            _log(data, who, _describe(changed), it["id"])
        elif kind == "add" and op.get("coll") == "experiments":
            fields = _clean_exp(dict(op["fields"]))
            ex = {"id": _next_id(exps, "X"), "title": "", "status": "planned", "outcome": "", "owner": "",
                  "problem": "", "setup": "", "location": "", "results": "", "related": [], "tags": [],
                  "notes": [], "started": "", "ended": "", "created": now(), "updated": now()}
            _exp_times(fields, ex)
            ex.update(fields)
            exps.append(ex)
            _log(data, who, f"added experiment “{ex['title']}”", ex["id"])
        elif kind == "add":
            fields = _clean_fields(dict(op["fields"]))
            it = {
                "id": _next_id(items, "N"), "title": "", "category": (data["meta"].get("categories") or ["Inbox"])[0],
                "priority": "P2", "kind": "", "status": "todo", "progress": 0, "owner": "", "location": "",
                "anchor": "", "issue": "", "fix": "", "sources": [], "tex": [], "tags": [],
                "notes": [], "created": now(), "updated": now(),
            }
            it.update(fields)
            items.append(it)
            _log(data, who, f"added “{it['title']}”", it["id"])
        elif kind == "delete":
            it, is_exp = _locate(data, op["id"])
            (exps if is_exp else items).remove(it)
            _log(data, who, f"deleted “{it['title']}”", it["id"])
        elif kind == "note":
            it = _locate(data, op["id"])[0]
            text = op["text"].strip()
            if not text:
                raise ValueError("empty note")
            it.setdefault("notes", []).append({"who": who, "at": now(), "text": text})
            it["updated"] = now()
            _log(data, who, "commented: " + (text[:80] + "…" if len(text) > 80 else text), it["id"])
        elif kind == "thought":
            text = op["text"].strip()
            if not text:
                raise ValueError("empty thought")
            th = {"id": _next_id(thoughts, "T"), "who": who, "at": now(), "text": text, "done": False}
            thoughts.insert(0, th)
            _log(data, who, "new thought: " + (text[:80] + "…" if len(text) > 80 else text))
        elif kind == "thought_update":
            th = _find(thoughts, op["id"])
            th.update({k: v for k, v in op["fields"].items() if k in ("text", "done")})
        elif kind == "thought_delete":
            thoughts.remove(_find(thoughts, op["id"]))
        elif kind == "meta":
            fields = _clean_meta(op["fields"])
            changed = {k: v for k, v in fields.items() if data["meta"].get(k) != v}
            if not changed:
                return data
            data["meta"].update(changed)
            _log(data, who, "project: " + _describe(changed))
        else:
            raise ValueError(f"unknown op: {kind}")
        data["meta"]["rev"] = data["meta"].get("rev", 0) + 1
        data["meta"]["updated"] = now()
        data["meta"]["updated_by"] = who
        save(slug, data)
        return data


def create_project(fields, who="me"):
    """Create a project from meta fields (+ optional "slug"). Returns its slug."""
    fields = dict(fields)
    want = slugify(str(fields.pop("slug", "") or fields.get("title", "")))
    meta = _clean_meta(fields)
    if "title" not in meta:
        raise ValueError("a project needs a title")
    with locked(None):
        slug, n = want, 2
        while exists(slug):
            slug, n = f"{want[:44]}-{n}", n + 1
        meta.setdefault("color", COLORS[len(slugs()) % len(COLORS)])
        data = empty_board(**meta)
        data["meta"]["updated_by"] = who
        _log(data, who, f"created project “{meta['title']}”")
        save(slug, data)
    return slug


def delete_project(slug):
    """Move a project file to trash/ (recoverable by moving it back)."""
    with locked(slug):
        p = path(slug)
        if not os.path.exists(p):
            raise KeyError(f"no such project: {slug}")
        os.makedirs(TRASH, exist_ok=True)
        os.replace(p, os.path.join(TRASH, f"{slug}.{datetime.now():%Y%m%d-%H%M%S}.json"))


def stats(data):
    w = {"P1": 3, "P2": 2, "P3": 1}
    live = [i for i in data["items"] if i["status"] != "skip"]
    tot = sum(w[i["priority"]] for i in live) or 1
    pct = sum(w[i["priority"]] * (100 if i["status"] == "done" else i.get("progress", 0)) for i in live) / tot
    by = {s: sum(1 for i in data["items"] if i["status"] == s) for s in STATUSES}
    ex = {s: sum(1 for x in data.get("experiments", []) if x["status"] == s) for s in EXP_STATUSES}
    return {"percent": round(pct, 1), "counts": by, "total": len(data["items"]), "experiments": ex,
            "open_thoughts": sum(1 for t in data.get("thoughts", []) if not t.get("done"))}


def summary(slug):
    data = load(slug)
    m = data["meta"]
    keep = ("slug", "title", "description", "icon", "color", "archived", "created", "updated", "updated_by", "rev")
    s = {k: m.get(k) for k in keep}
    s["stats"] = stats(data)
    s["decide"] = [{"id": i["id"], "title": i["title"], "priority": i["priority"]}
                   for i in data["items"] if i["status"] == "decide"]
    return s


def list_projects():
    """Project summaries: active first, most recently updated first."""
    out = []
    for slug in slugs():
        try:
            out.append(summary(slug))
        except (OSError, ValueError, KeyError):
            continue
    out.sort(key=lambda p: p["updated"] or "", reverse=True)
    out.sort(key=lambda p: bool(p["archived"]))
    return out


def version(slug):
    """Cheap change token: rev plus mtime, so hand edits to the file also count."""
    try:
        rev = load(slug)["meta"].get("rev", 0)
        return f"{rev}:{os.path.getmtime(path(slug)):.3f}"
    except (json.JSONDecodeError, OSError, KeyError):
        return "gone"


def index_version():
    """Change token for the whole home (any project created, deleted or edited)."""
    h = hashlib.sha1()
    for slug in slugs():
        try:
            h.update(f"{slug}:{os.path.getmtime(path(slug)):.3f};".encode())
        except OSError:
            pass
    return h.hexdigest()[:16]
