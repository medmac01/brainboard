"""Shared storage + operations for brainboard.

The board file (BRAINBOARD_DATA, default ./data/board.json) is the single source of truth. Every writer (web server, CLI,
any agent) goes through apply_op(), which takes a file lock, re-reads the
file, applies one operation, bumps `rev`, logs activity and writes atomically.
Agents may also edit data.json by hand; the web page picks up any change.
"""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.environ.get("BRAINBOARD_DATA", os.path.join(HERE, "..", "data", "board.json")))
DATA_DIR = os.path.dirname(DATA)
LOCK = os.path.join(DATA_DIR, ".data.lock")

DEFAULT_CATEGORIES = ["Inbox", "Writing", "Experiments", "Figures", "Admin"]

STATUSES = ["todo", "doing", "decide", "blocked", "done", "skip"]
PRIORITIES = ["P1", "P2", "P3"]
ITEM_FIELDS = {
    "title", "category", "priority", "kind", "status", "progress", "owner",
    "location", "anchor", "issue", "fix", "sources", "tex", "tags", "pinned",
}
ACTIVITY_CAP = 500


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def empty_board(title="My board"):
    return {
        "meta": {"title": title, "rev": 0, "updated": now(), "updated_by": "brainboard",
                 "sources": {}, "categories": DEFAULT_CATEGORIES, "people": ["me", "claude", "agent"]},
        "items": [], "thoughts": [], "activity": [],
    }


def ensure_board():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(DATA):
        save(empty_board(os.environ.get("BRAINBOARD_TITLE", "My board")))


@contextmanager
def locked():
    with open(LOCK, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load():
    with open(DATA, encoding="utf-8") as fh:
        return json.load(fh)


def save(data):
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".data.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, DATA)


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


def _describe(fields):
    parts = []
    for k, v in fields.items():
        if k in ("issue", "fix", "anchor"):
            parts.append(f"edited {k}")
        elif isinstance(v, str) and len(v) > 40:
            parts.append(f"edited {k}")
        else:
            parts.append(f"{k} → {v}")
    return ", ".join(parts)


def apply_op(op, who="me"):
    """Apply one operation dict. Returns the new full state."""
    kind = op.get("op")
    with locked():
        data = load()
        items, thoughts = data["items"], data.setdefault("thoughts", [])
        if kind == "update":
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
        elif kind == "add":
            fields = _clean_fields(dict(op["fields"]))
            it = {
                "id": _next_id(items, "N"), "title": "", "category": (data["meta"].get("categories") or ["Inbox"])[0], "priority": "P2",
                "kind": "", "status": "todo", "progress": 0, "owner": "", "location": "",
                "anchor": "", "issue": "", "fix": "", "sources": [], "tex": [], "tags": [],
                "notes": [], "created": now(), "updated": now(),
            }
            it.update(fields)
            items.append(it)
            _log(data, who, f"added “{it['title']}”", it["id"])
        elif kind == "delete":
            it = _find(items, op["id"])
            items.remove(it)
            _log(data, who, f"deleted “{it['title']}”", it["id"])
        elif kind == "note":
            it = _find(items, op["id"])
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
        else:
            raise ValueError(f"unknown op: {kind}")
        data["meta"]["rev"] = data["meta"].get("rev", 0) + 1
        data["meta"]["updated"] = now()
        data["meta"]["updated_by"] = who
        save(data)
        return data


def stats(data):
    w = {"P1": 3, "P2": 2, "P3": 1}
    live = [i for i in data["items"] if i["status"] != "skip"]
    tot = sum(w[i["priority"]] for i in live) or 1
    pct = sum(w[i["priority"]] * (100 if i["status"] == "done" else i.get("progress", 0)) for i in live) / tot
    by = {s: sum(1 for i in data["items"] if i["status"] == s) for s in STATUSES}
    return {"percent": round(pct, 1), "counts": by, "total": len(data["items"])}



def version():
    """Cheap change token: rev plus mtime, so hand edits to data.json also count."""
    try:
        rev = load()["meta"].get("rev", 0)
    except (json.JSONDecodeError, OSError, KeyError):
        rev = -1
    return f"{rev}:{os.path.getmtime(DATA):.3f}"
