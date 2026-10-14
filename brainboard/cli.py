#!/usr/bin/env python3
"""Command-line access to brainboard, for agents (and humans).

Works on a local board file, or on a remote server when a URL is configured:
  BRAINBOARD_URL=http://host:2001 BRAINBOARD_PASSWORD=… python3 brainboard/cli.py summary
or put {"url": …, "password": …, "who": …} in ~/.config/brainboard/config.json (chmod 600).

Examples
  python3 brainboard/cli.py summary
  python3 brainboard/cli.py next                     # what to work on
  python3 brainboard/cli.py list --status todo,doing --priority P1
  python3 brainboard/cli.py show E03
  python3 brainboard/cli.py set E03 status=doing progress=30 owner=claude
  python3 brainboard/cli.py note E03 "Code uses /13 because ..."
  python3 brainboard/cli.py add "Fix Fig. 9 legend" --cat "Figures, tables & refs" --priority P3
  python3 brainboard/cli.py thought "Ask prof whether to keep APTMorph"
  python3 brainboard/cli.py thoughts

Author defaults to "claude"; override with --who or TRACKER_WHO.
"""
import argparse
import base64
import json
import os
import sys
import textwrap
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

CONFIG = os.path.expanduser("~/.config/brainboard/config.json")


def _config():
    cfg = {}
    if os.path.exists(CONFIG):
        with open(CONFIG) as fh:
            cfg = json.load(fh)
    url = os.environ.get("BRAINBOARD_URL", cfg.get("url", ""))
    pw = os.environ.get("BRAINBOARD_PASSWORD", cfg.get("password", ""))
    who = os.environ.get("TRACKER_WHO") or os.environ.get("BRAINBOARD_WHO") or cfg.get("who", "claude")
    return url.rstrip("/"), pw, who


URL, PASSWORD, WHO = _config()


def _http(path, body=None):
    req = urllib.request.Request(URL + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    if PASSWORD:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"cli:{PASSWORD}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise ValueError(f"server said {e.code}: {e.read().decode()[:200]}")


def load():
    if URL:
        return _http("/api/state")
    core.ensure_board()
    return core.load()


def apply_op(op, who):
    if URL:
        return _http("/api/op", dict(op, who=who))
    return core.apply_op(op, who)

PRI_ORDER = {"P1": 0, "P2": 1, "P3": 2}
STATUS_ORDER = {"decide": 0, "doing": 1, "blocked": 2, "todo": 3, "done": 4, "skip": 5}


def line(it):
    own = f" @{it['owner']}" if it.get("owner") else ""
    return f"{it['id']:<4} {it['priority']} {it['status']:<7} {it.get('progress', 0):>3}%  {it['title']}{own}"


def cmd_list(a, data):
    items = data["items"]
    if a.status:
        items = [i for i in items if i["status"] in a.status.split(",")]
    if a.priority:
        items = [i for i in items if i["priority"] in a.priority.split(",")]
    if a.owner:
        items = [i for i in items if i.get("owner") == a.owner]
    if a.cat:
        items = [i for i in items if a.cat.lower() in i["category"].lower()]
    if a.json:
        return print(json.dumps(items, indent=1, ensure_ascii=False))
    for it in items:
        print(line(it))


def cmd_show(a, data):
    it = core._find(data["items"], a.id)
    if a.json:
        return print(json.dumps(it, indent=1, ensure_ascii=False))
    print(line(it))
    for k in ("category", "kind", "location", "sources", "tex"):
        if it.get(k):
            print(f"  {k:<9} {', '.join(it[k]) if isinstance(it[k], list) else it[k]}")
    for k in ("anchor", "issue", "fix"):
        if it.get(k):
            print(f"\n  {k.upper()}\n" + textwrap.indent(textwrap.fill(it[k], 96), "    "))
    for n in it.get("notes", []):
        print(f"\n  [{n['at'][:16]} {n['who']}]\n" + textwrap.indent(textwrap.fill(n["text"], 96), "    "))


def cmd_set(a, data):
    fields = {}
    for kv in a.pairs:
        k, _, v = kv.partition("=")
        if k in ("sources", "tex", "tags"):
            fields[k] = [x for x in v.split(",") if x]
        elif k == "pinned":
            fields[k] = v.lower() in ("1", "true", "yes")
        else:
            fields[k] = v
    print(line(core._find(apply_op({"op": "update", "id": a.id, "fields": fields}, a.who)["items"], a.id)))


def cmd_note(a, data):
    apply_op({"op": "note", "id": a.id, "text": a.text}, a.who)
    print(f"noted on {a.id}")


def cmd_add(a, data):
    f = {"title": a.title, "priority": a.priority}
    if a.cat:
        f["category"] = a.cat
    for k in ("issue", "fix", "location", "owner"):
        if getattr(a, k):
            f[k] = getattr(a, k)
    new = apply_op({"op": "add", "fields": f}, a.who)
    print(line(new["items"][-1]))


def cmd_thought(a, data):
    apply_op({"op": "thought", "text": a.text}, a.who)
    print("thought added")


def cmd_thoughts(a, data):
    for t in data.get("thoughts", []):
        mark = "x" if t.get("done") else " "
        print(f"[{mark}] {t['id']} {t['at'][:16]} {t['who']}: {t['text']}")


def cmd_summary(a, data):
    s = core.stats(data)
    print(f"Overall {s['percent']}% (priority-weighted)  ·  {s['total']} items")
    print("  " + "  ".join(f"{k}:{v}" for k, v in s["counts"].items()))
    cats = {}
    for it in data["items"]:
        c = cats.setdefault(it["category"], [0, 0])
        c[0] += 1
        c[1] += it["status"] in ("done", "skip")
    for c, (n, d) in cats.items():
        print(f"  {c:<34} {d}/{n}")


def cmd_next(a, data):
    live = [i for i in data["items"] if i["status"] not in ("done", "skip")]
    live.sort(key=lambda i: (STATUS_ORDER[i["status"]], PRI_ORDER[i["priority"]], i["id"]))
    for it in live[: a.n]:
        print(line(it))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--who", default=WHO)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--status"); p.add_argument("--priority")
    p.add_argument("--owner"); p.add_argument("--cat"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("show"); p.add_argument("id"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("set"); p.add_argument("id"); p.add_argument("pairs", nargs="+", metavar="field=value")
    p = sub.add_parser("note"); p.add_argument("id"); p.add_argument("text")
    p = sub.add_parser("add"); p.add_argument("title"); p.add_argument("--cat")
    p.add_argument("--priority", default="P2"); p.add_argument("--issue"); p.add_argument("--fix")
    p.add_argument("--location"); p.add_argument("--owner")
    p = sub.add_parser("thought"); p.add_argument("text")
    sub.add_parser("thoughts")
    sub.add_parser("summary")
    p = sub.add_parser("next"); p.add_argument("-n", type=int, default=8)
    a = ap.parse_args()
    try:
        globals()["cmd_" + a.cmd](a, load())
    except (KeyError, ValueError, OSError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
