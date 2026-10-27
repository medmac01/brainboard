#!/usr/bin/env python3
"""Command-line access to brainboard, for agents (and humans).

Works on a local data directory, or on a remote server when a URL is configured:
  BRAINBOARD_URL=http://host:2001 BRAINBOARD_PASSWORD=… python3 brainboard/cli.py projects
or run `cli.py config --url http://host:2001` once (writes ~/.config/brainboard/config.json, chmod 600).

Which project? First match wins: -p/--project, $BRAINBOARD_PROJECT, a `.brainboard` file in this
directory or a parent (one line: the slug), the config default (`cli.py use SLUG`), or the only
active project.

Examples
  python3 brainboard/cli.py projects
  python3 brainboard/cli.py -p thesis summary
  python3 brainboard/cli.py inbox                    # notes and thoughts waiting for *you* (per item)
  python3 brainboard/cli.py next                     # what to work on
  python3 brainboard/cli.py list --status todo,doing --priority P1
  python3 brainboard/cli.py show E03
  python3 brainboard/cli.py set E03 status=doing progress=30 owner=claude
  python3 brainboard/cli.py note E03 "Code uses /13 because ..."
  python3 brainboard/cli.py add "Fix Fig. 9 legend" --cat Writing --priority P3
  python3 brainboard/cli.py exp-add "Rerun baseline with seed 7" --problem "Is the gain seed noise?" --where gpu-box:~/runs
  python3 brainboard/cli.py set X01 status=running        # then: set X01 status=done outcome=worked results="…"
  python3 brainboard/cli.py thought "Ask whether to keep the baseline"
  python3 brainboard/cli.py project-add "Thesis" --icon 🎓 --cats "Chapters,Experiments,Admin"

Paper library (shared by all projects; P ids work with show/set/note too)
  python3 brainboard/cli.py -p thesis papers                  # list (all without -p); -q filters titles/tags
  python3 brainboard/cli.py paper-add arxiv:2401.12345 10.1145/3576915.3623100 --projects thesis
  python3 brainboard/cli.py paper-add --pdf ~/Downloads/*.pdf --tags provenance
  python3 brainboard/cli.py show P012                         # details, card, summary, notes
  python3 brainboard/cli.py paper-text P012 --pages 3-5       # full text with page markers
  python3 brainboard/cli.py grep '"subgraph matching" LLM'    # full-text search across the library
  python3 brainboard/cli.py paper-card P012 --file card.json  # write the structured digest
  python3 brainboard/cli.py -p thesis bib > refs.bib

Author defaults to "claude"; override with --who or BRAINBOARD_WHO.
"""
import argparse
import base64
import getpass
import json
import os
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402
import library  # noqa: E402

CONFIG = os.path.expanduser("~/.config/brainboard/config.json")


def _read_config():
    if os.path.exists(CONFIG):
        with open(CONFIG) as fh:
            return json.load(fh)
    return {}


CFG = _read_config()
URL = os.environ.get("BRAINBOARD_URL", CFG.get("url", "")).rstrip("/")
PASSWORD = os.environ.get("BRAINBOARD_PASSWORD", CFG.get("password", ""))
WHO = os.environ.get("BRAINBOARD_WHO") or os.environ.get("TRACKER_WHO") or CFG.get("who", "claude")


def _write_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(cfg, fh, indent=1)
        fh.write("\n")


# ---------- backend: remote server or local files ----------

def _http(path, body=None, raw=None, ctype="application/json", timeout=20, parse=True):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(URL + path, data=data, headers={"Content-Type": ctype})
    if PASSWORD:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"cli:{PASSWORD}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r) if parse else r.read()
    except urllib.error.HTTPError as e:
        raise ValueError(f"server said {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        raise ValueError(f"can't reach {URL}: {e.reason}")


def list_projects():
    if URL:
        return _http("/api/projects")["projects"]
    core.ensure_home()
    return core.list_projects()


def load(slug):
    if URL:
        return _http(f"/api/p/{urllib.parse.quote(slug)}/state")
    return core.load(slug)


def apply_op(slug, op, who):
    if URL:
        return _http(f"/api/p/{urllib.parse.quote(slug)}/op", dict(op, who=who))
    return core.apply_op(slug, op, who)


def create_project(fields, who):
    if URL:
        return _http("/api/projects", {"op": "create", "fields": fields, "who": who})["slug"]
    core.ensure_home()
    return core.create_project(fields, who)


# ---------- library backend ----------

def lib_load():
    return _http("/api/library") if URL else library.load()


def lib_op(op, who):
    return _http("/api/library/op", dict(op, who=who)) if URL else library.apply_op(op, who)


def lib_add_pdf(path, who, fields):
    with open(path, "rb") as fh:
        raw = fh.read()
    if URL:
        q = urllib.parse.urlencode(dict(fields, who=who))
        r = _http("/api/library/upload?" + q, raw=raw, ctype="application/pdf", timeout=180)
        return r["id"], r["existed"]
    _, pid, existed = library.add_pdf(raw, path, who, library._clean_paper(fields))
    return pid, existed


def lib_attach(pid, path, who):
    with open(path, "rb") as fh:
        raw = fh.read()
    if URL:
        return _http(f"/api/library/upload?id={pid}&who={urllib.parse.quote(who)}", raw=raw, ctype="application/pdf", timeout=180)
    return library.attach_pdf(pid, raw, who)


def lib_import(ref, who, fields):
    if URL:
        r = _http("/api/library/import", dict(fields, ref=ref, who=who), timeout=180)
        return r["id"], r["existed"]
    _, pid, existed = library.import_ref(ref, who, library._clean_paper(fields))
    return pid, existed


def lib_import_bib(src, who, fields):
    if URL:
        r = _http("/api/library/bib", dict(fields, bib=src, who=who), timeout=60)
        return r["added"], r["skipped"]
    _, added, skipped = library.import_bib(src, who, fields.get("tags"), fields.get("projects"))
    return added, skipped


def lib_text(pid, pages):
    if URL:
        return _http(f"/api/library/text/{pid}?pages={urllib.parse.quote(pages or '')}")
    return library.text(pid, pages)


def lib_grep(q, project, limit):
    if URL:
        return _http("/api/library/grep?" + urllib.parse.urlencode({"q": q, "project": project or "", "limit": limit}), timeout=60)["hits"]
    return library.grep(q, project=project or "", limit=limit)


def lib_pdf(pid):
    if URL:
        return _http(f"/api/library/pdf/{pid}", parse=False, timeout=120)
    with open(library.pdf_path(pid), "rb") as fh:
        return fh.read()


# ---------- project resolution ----------

def _pin_file():
    d = os.getcwd()
    while True:
        f = os.path.join(d, ".brainboard")
        if os.path.isfile(f):
            with open(f) as fh:
                words = fh.read().split()
            return words[0] if words else ""
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent


def _match(spec, projects):
    s = spec.lower()
    for test in (lambda p: p["slug"] == s, lambda p: p["title"].lower() == s):
        hit = [p for p in projects if test(p)]
        if hit:
            return hit[0]["slug"]
    hit = [p for p in projects if p["slug"].startswith(s)]
    if len(hit) == 1:
        return hit[0]["slug"]
    known = ", ".join(p["slug"] for p in projects) or "none yet; create one with project-add"
    raise ValueError(f"no single project matches {spec!r} (projects: {known})")


def resolve(a):
    projects = list_projects()
    spec = a.project or os.environ.get("BRAINBOARD_PROJECT") or _pin_file() or CFG.get("project", "")
    if spec:
        return _match(spec, projects)
    live = [p for p in projects if not p.get("archived")]
    if len(live) == 1:
        return live[0]["slug"]
    if not live:
        raise ValueError("no projects yet; create one: cli.py project-add \"Title\"")
    raise ValueError("several projects; pick one with -p SLUG or `cli.py use SLUG`: "
                     + ", ".join(p["slug"] for p in live))


# ---------- output helpers ----------

PRI_ORDER = {"P1": 0, "P2": 1, "P3": 2}
STATUS_ORDER = {"decide": 0, "doing": 1, "blocked": 2, "todo": 3, "done": 4, "skip": 5}


OUTCOME_LABEL = {"worked": "worked", "inconclusive": "inconclusive", "didnt": "didn't work"}


def line(it):
    if it["id"].startswith("X") and "outcome" in it:
        out = f" [{OUTCOME_LABEL[it['outcome']]}]" if it.get("outcome") else ""
        own = f" @{it['owner']}" if it.get("owner") else ""
        return f"{it['id']:<4} exp {it['status']:<8}  {it['title']}{out}{own}"
    own = f" @{it['owner']}" if it.get("owner") else ""
    return f"{it['id']:<4} {it['priority']} {it['status']:<7} {it.get('progress', 0):>3}%  {it['title']}{own}"


def wrap(text, indent="    "):
    return "\n".join(textwrap.indent(textwrap.fill(p, 96), indent) if p.strip() else ""
                     for p in text.split("\n"))


# ---------- project commands ----------

def cmd_projects(a):
    projects = list_projects()
    if a.json:
        return print(json.dumps(projects, indent=1, ensure_ascii=False))
    try:
        cur = resolve(a)
    except ValueError:
        cur = ""
    if not projects:
        return print("no projects yet; create one: cli.py project-add \"Title\"")
    for p in projects:
        s = p["stats"]
        opn = s["total"] - s["counts"]["done"] - s["counts"]["skip"]
        flags = " (archived)" if p.get("archived") else ""
        dec = f"  {len(p['decide'])} to decide" if p.get("decide") else ""
        print(f"{'*' if p['slug'] == cur else ' '} {p['slug']:<28} {s['percent']:>5.1f}%  {opn:>3} open{dec}  "
              f"{p.get('icon') or ''} {p['title']}{flags}")


def cmd_project_add(a):
    f = {"title": a.title}
    for k, v in (("slug", a.slug), ("description", a.desc), ("icon", a.icon), ("color", a.color),
                 ("categories", a.cats), ("people", a.people)):
        if v:
            f[k] = v
    print("created", create_project(f, a.who))


def cmd_project_set(a):
    slug = resolve(a)
    fields = dict(kv.partition("=")[::2] for kv in a.pairs)
    m = apply_op(slug, {"op": "meta", "fields": fields}, a.who)["meta"]
    print(f"{slug}: " + ", ".join(f"{k}={m.get(k)}" for k in fields))


def cmd_use(a):
    _match(a.slug, list_projects())
    cfg = _read_config()
    cfg["project"] = a.slug
    _write_config(cfg)
    print(f"default project → {a.slug}  (in {CONFIG})")


def cmd_config(a):
    cfg = _read_config()
    if a.url is not None:
        cfg["url"] = a.url.rstrip("/")
        pw = getpass.getpass("Board password (empty for none): ") if sys.stdin.isatty() else sys.stdin.readline().strip()
        if pw:
            cfg["password"] = pw
        else:
            cfg.pop("password", None)
    if a.set_who:
        cfg["who"] = a.set_who
    if a.url is not None or a.set_who:
        _write_config(cfg)
    shown = {k: ("••••" if k == "password" else v) for k, v in cfg.items()}
    print(f"{CONFIG}: {json.dumps(shown)}")


# ---------- item commands (inside one project) ----------

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
    it = core._locate(data, a.id)[0]
    if a.json:
        return print(json.dumps(it, indent=1, ensure_ascii=False))
    print(line(it))
    for k in ("category", "kind", "location", "sources", "tex", "tags", "related", "started", "ended"):
        if it.get(k):
            print(f"  {k:<9} {', '.join(it[k]) if isinstance(it[k], list) else it[k]}")
    for k in ("anchor", "issue", "fix", "problem", "setup", "results"):
        if it.get(k):
            print(f"\n  {k.upper()}\n" + wrap(it[k]))
    for n in it.get("notes", []):
        print(f"\n  [{n['at'][:16]} {n['who']}]\n" + wrap(n["text"]))


def cmd_set(a, data):
    fields = {}
    for kv in a.pairs:
        k, _, v = kv.partition("=")
        if k == "where":
            k = "location"
        if k in ("sources", "tex", "tags", "related"):
            fields[k] = [x for x in v.split(",") if x]
        elif k == "pinned":
            fields[k] = v.lower() in ("1", "true", "yes")
        else:
            fields[k] = v
    print(line(core._locate(apply_op(a.slug, {"op": "update", "id": a.id, "fields": fields}, a.who), a.id)[0]))


def cmd_note(a, data):
    apply_op(a.slug, {"op": "note", "id": a.id, "text": a.text}, a.who)
    print(f"noted on {a.id}")


def cmd_add(a, data):
    f = {"title": a.title, "priority": a.priority}
    if a.cat:
        f["category"] = a.cat
    for k in ("issue", "fix", "location", "owner"):
        if getattr(a, k):
            f[k] = getattr(a, k)
    new = apply_op(a.slug, {"op": "add", "fields": f}, a.who)
    print(line(new["items"][-1]))


def cmd_thought(a, data):
    apply_op(a.slug, {"op": "thought", "text": a.text}, a.who)
    print("thought added")


def cmd_thoughts(a, data):
    for t in data.get("thoughts", []):
        if t.get("done") and not a.all:
            continue
        mark = "x" if t.get("done") else " "
        print(f"[{mark}] {t['id']} {t['at'][:16]} {t['who']}: {t['text']}")


def cmd_inbox(a, data):
    """Per item: notes by the person (or --from) posted after my own last note on that item, plus their open thoughts."""
    me, found = a.who, False
    frm = None if a.frm == "all" else set(a.frm.split(","))
    asks = lambda who: who != me and (frm is None or who in frm)
    for it in data["items"] + data.get("experiments", []):
        notes = it.get("notes", [])
        mine = [k for k, n in enumerate(notes) if n["who"] == me]
        start = mine[-1] + 1 if mine else 0
        waiting = [n for n in notes[start:] if asks(n["who"])]
        if not waiting:
            continue
        found = True
        print(line(it))
        for n in waiting:
            print(f"  [{n['at'][:16]} {n['who']}]\n" + wrap(n["text"]))
        print()
    th = [t for t in data.get("thoughts", []) if not t.get("done") and asks(t["who"])]
    if th:
        found = True
        print("Open thoughts:")
        for t in th:
            print(f"  {t['id']} {t['at'][:16]} {t['who']}: {t['text']}")
    if not found:
        print(f"nothing waiting for {me}")


def cmd_exp(a, data):
    exps = data.get("experiments", [])
    if a.status:
        exps = [x for x in exps if x["status"] in a.status.split(",")]
    if a.json:
        return print(json.dumps(exps, indent=1, ensure_ascii=False))
    order = {s: k for k, s in enumerate(["running", "planned", "done", "failed", "dropped"])}
    for x in sorted(exps, key=lambda x: (order[x["status"]], x["id"])):
        print(line(x))
    if not exps:
        print("no experiments" + (" with that status" if a.status else " yet; add one with exp-add"))


def cmd_exp_add(a, data):
    f = {"title": a.title, "status": a.status}
    for k, v in (("problem", a.problem), ("setup", a.setup), ("location", a.where),
                 ("related", a.related), ("owner", a.owner)):
        if v:
            f[k] = v
    new = apply_op(a.slug, {"op": "add", "coll": "experiments", "fields": f}, a.who)
    print(line(new["experiments"][-1]))


def cmd_summary(a, data):
    s = core.stats(data)
    m = data["meta"]
    print(f"{m.get('icon', '')} {m['title']}  [{a.slug}]")
    print(f"Overall {s['percent']}% (priority-weighted)  ·  {s['total']} items  ·  {s['open_thoughts']} open thoughts")
    print("  " + "  ".join(f"{k}:{v}" for k, v in s["counts"].items()))
    if any(s["experiments"].values()):
        print("  experiments  " + "  ".join(f"{k}:{v}" for k, v in s["experiments"].items() if v))
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


# ---------- paper library (no project needed) ----------

def pline(p):
    who = (p["authors"][0].split()[-1] + (" et al." if len(p["authors"]) > 1 else "")) if p.get("authors") else "?"
    short = f"{p['short']}: " if p.get("short") and not p["title"].startswith(p["short"]) else ""
    flags = ("" if p.get("pdf") else " [no pdf]") + (" [card]" if p.get("card", {}).get("tldr") else "")
    return f"{p['id']:<5} {p.get('reading', ''):<8} {who} {p.get('year') or ''} · {short}{p['title']}{flags}"


def _lib_fields(a):
    f = {}
    for k in ("tags", "projects", "reading"):
        v = getattr(a, k, None)
        if v:
            f[k] = v
    return f


def cmd_papers(a):
    ps = lib_load()["papers"]
    if a.project:
        ps = [p for p in ps if a.project in p.get("projects", [])]
    if a.tag:
        ps = [p for p in ps if a.tag in p.get("tags", [])]
    if a.reading:
        ps = [p for p in ps if p.get("reading") in a.reading.split(",")]
    if a.q:
        ql = a.q.lower()
        ps = [p for p in ps if ql in " ".join([p["title"], p.get("short", ""), " ".join(p.get("tags", [])),
                                                " ".join(p.get("authors", [])), p.get("venue", "")]).lower()]
    if a.json:
        return print(json.dumps(ps, indent=1, ensure_ascii=False))
    for p in ps:
        print(pline(p))
    if not ps:
        print("no papers" + (" match" if (a.q or a.project or a.tag or a.reading) else " yet; add some with paper-add"))


def show_paper(p, as_json=False):
    if as_json:
        return print(json.dumps(p, indent=1, ensure_ascii=False))
    print(pline(p))
    for k in ("authors", "venue", "doi", "arxiv", "url", "bibkey", "tags", "projects"):
        if p.get(k):
            print(f"  {k:<9} {', '.join(p[k]) if isinstance(p[k], list) else p[k]}")
    if p.get("pdf"):
        print(f"  {'pdf':<9} {p.get('pages', 0)} pages  (paper-text {p['id']} --pages 1-3)")
    for k in ("abstract", "summary"):
        if p.get(k):
            print(f"\n  {k.upper()}\n" + wrap(p[k]))
    card = p.get("card") or {}
    if card:
        print(f"\n  CARD  (by {card.get('by', '?')}, {card.get('at', '')[:10]})")
        for k in library.CARD_TEXT + library.CARD_LISTS:
            if card.get(k):
                v = ", ".join(card[k]) if isinstance(card[k], list) else card[k]
                print(f"  · {k}\n" + wrap(v, "      "))
    for n in p.get("notes", []):
        print(f"\n  [{n['at'][:16]} {n['who']}]\n" + wrap(n["text"]))


def cmd_paper_add(a):
    f = _lib_fields(a)
    if a.bib:
        with open(os.path.expanduser(a.bib), encoding="utf-8") as fh:
            added, skipped = lib_import_bib(fh.read(), a.who, f)
        print(f"added {len(added)}: {' '.join(added) or '-'}; already there: {' '.join(skipped) or '-'}")
    for path in a.pdf or []:
        try:
            pid, existed = lib_add_pdf(os.path.expanduser(path), a.who, f)
            print(f"{'exists' if existed else 'added '} {pid}  ← {os.path.basename(path)}")
        except (ValueError, OSError) as e:
            print(f"failed  {os.path.basename(path)}: {e}")
    for ref in a.refs:
        try:
            pid, existed = lib_import(ref, a.who, f)
            print(f"{'exists' if existed else 'added '} {pid}  ← {ref}")
        except ValueError as e:
            print(f"failed  {ref}: {e}")
    if not (a.bib or a.pdf or a.refs):
        sys.exit("give arXiv ids / DOIs / links, --pdf FILE…, or --bib FILE")


def _paper_set_fields(pairs):
    fields = {}
    for kv in pairs:
        k, _, v = kv.partition("=")
        fields[k] = v
    return fields


def cmd_paper_set(a):
    st = lib_op({"op": "paper_update", "id": a.id, "fields": _paper_set_fields(a.pairs)}, a.who)
    print(pline(core._find(st["papers"], a.id)))


def cmd_paper_card(a):
    if a.file:
        with open(os.path.expanduser(a.file), encoding="utf-8") as fh:
            card = json.load(fh)
    else:
        card = _paper_set_fields(a.pairs)
    extra = {k: card.pop(k) for k in list(card) if k in library.PAPER_FIELDS}   # a card file may carry short/tags/…
    fields = dict(extra, card=card) if card else extra
    st = lib_op({"op": "paper_update", "id": a.id, "fields": fields}, a.who)
    print(pline(core._find(st["papers"], a.id)))


def cmd_paper_text(a):
    t = lib_text(a.id, a.pages)
    print(t["text"])
    if not a.pages and t["pages"] > 3:
        print(f"\n[{t['pages']} pages; use --pages to read part of it]", file=sys.stderr)


def cmd_paper_pdf(a):
    raw = lib_pdf(a.id)
    out = a.out or f"{a.id}.pdf"
    with open(out, "wb") as fh:
        fh.write(raw)
    print(f"wrote {out} ({len(raw) // 1024} KB)")


def cmd_paper_attach(a):
    lib_attach(a.id, os.path.expanduser(a.file), a.who)
    print(f"attached {a.file} to {a.id}")


def cmd_paper_delete(a):
    lib_op({"op": "paper_delete", "id": a.id}, a.who)
    print(f"deleted {a.id} (files kept in library/trash)")


def cmd_grep(a):
    hits = lib_grep(a.query, a.project, a.n)
    for h in hits:
        where = "meta" if h["page"] == "meta" else f"p.{h['page']}"
        print(f"{h['id']:<5} {where:<5} ×{h['count']:<3} {h['short'][:28]:<28}  …{h['snippet']}…")
    if not hits:
        print("no matches")


def cmd_bib(a):
    if URL:
        q = urllib.parse.urlencode({"ids": ",".join(a.ids), "project": a.project or ""})
        return sys.stdout.write(_http("/api/library/bib?" + q, parse=False).decode())
    ps = [p for p in library.load()["papers"] if (not a.ids or p["id"] in a.ids)
          and (not a.project or a.project in p.get("projects", []))]
    sys.stdout.write(library.bibtex(ps))


LIB_ID = lambda x: bool(x) and x[0] in "PI" and x[1:].isdigit()


def lib_route(a):
    """show/set/note with a P or I id go to the library instead of a project."""
    if a.cmd == "show":
        return show_paper(core._find(lib_load()["papers"] + lib_load().get("ideas", []), a.id), a.json)
    if a.cmd == "set":
        return cmd_paper_set(a)
    lib_op({"op": "note", "id": a.id, "text": a.text}, a.who)
    print(f"noted on {a.id}")


GLOBAL_CMDS = {"projects", "project_add", "use", "config", "papers", "paper_add", "paper_set", "paper_card",
               "paper_text", "paper_pdf", "paper_attach", "paper_delete", "grep", "bib"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--who", default=WHO, help="sign changes as (default: %(default)s)")
    ap.add_argument("-p", "--project", help="project slug or title")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("projects", help="list projects (* = the one commands use)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("project-add", help="create a project")
    p.add_argument("title"); p.add_argument("--slug"); p.add_argument("--desc"); p.add_argument("--icon")
    p.add_argument("--color", help="#rrggbb"); p.add_argument("--cats", help="comma-separated categories")
    p.add_argument("--people", help="comma-separated people/agents")
    p = sub.add_parser("project-set", help="edit project fields: title description icon color categories people archived")
    p.add_argument("pairs", nargs="+", metavar="field=value")
    p = sub.add_parser("use", help="set the default project in your config")
    p.add_argument("slug")
    p = sub.add_parser("config", help="show or set url/password/who in your config")
    p.add_argument("--url"); p.add_argument("--who", dest="set_who")
    p = sub.add_parser("summary")
    p = sub.add_parser("inbox", help="notes and thoughts waiting for you")
    p.add_argument("--from", dest="frm", default=CFG.get("human", "me"),
                   help="whose notes count, comma-separated, or 'all' (default: %(default)s)")
    p = sub.add_parser("next"); p.add_argument("-n", type=int, default=8)
    p = sub.add_parser("list"); p.add_argument("--status"); p.add_argument("--priority")
    p.add_argument("--owner"); p.add_argument("--cat"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("show"); p.add_argument("id"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("set"); p.add_argument("id"); p.add_argument("pairs", nargs="+", metavar="field=value")
    p = sub.add_parser("note"); p.add_argument("id"); p.add_argument("text")
    p = sub.add_parser("add"); p.add_argument("title"); p.add_argument("--cat")
    p.add_argument("--priority", default="P2"); p.add_argument("--issue"); p.add_argument("--fix")
    p.add_argument("--location"); p.add_argument("--owner")
    p = sub.add_parser("exp", help="list experiments (show/set/note take X ids too)")
    p.add_argument("--status", help="planned,running,done,failed,dropped"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("exp-add", help="add an experiment")
    p.add_argument("title"); p.add_argument("--status", default="planned", choices=core.EXP_STATUSES)
    p.add_argument("--problem", help="question / why"); p.add_argument("--setup", help="what is run, config, command")
    p.add_argument("--where", help="host, path, job id"); p.add_argument("--related", help="item ids, comma-separated")
    p.add_argument("--owner")
    p = sub.add_parser("papers", help="list the paper library")
    p.add_argument("-q", help="filter titles, authors, tags, venue"); p.add_argument("--tag")
    p.add_argument("--reading", help="to-read,skimmed,read,key"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("paper-add", help="add papers: arXiv ids/DOIs/links, --pdf files, or --bib file")
    p.add_argument("refs", nargs="*"); p.add_argument("--pdf", nargs="+"); p.add_argument("--bib")
    p.add_argument("--tags"); p.add_argument("--projects", help="project slugs, comma-separated")
    p.add_argument("--reading", choices=library.READING)
    p = sub.add_parser("paper-set", help="edit paper fields (title short authors year venue doi arxiv url abstract "
                                         "tags projects reading summary)")
    p.add_argument("id"); p.add_argument("pairs", nargs="+", metavar="field=value")
    p = sub.add_parser("paper-card", help="write a paper's card: --file card.json or key=value "
                                          f"({', '.join(library.CARD_TEXT + library.CARD_LISTS)})")
    p.add_argument("id"); p.add_argument("pairs", nargs="*", metavar="key=value"); p.add_argument("--file")
    p = sub.add_parser("paper-text", help="full text with page markers"); p.add_argument("id")
    p.add_argument("--pages", help="e.g. 1-3,7")
    p = sub.add_parser("paper-pdf", help="download the PDF"); p.add_argument("id"); p.add_argument("-o", "--out")
    p = sub.add_parser("paper-attach", help="attach a PDF to an existing paper"); p.add_argument("id"); p.add_argument("file")
    p = sub.add_parser("paper-delete"); p.add_argument("id")
    p = sub.add_parser("grep", help="full-text search across the library"); p.add_argument("query")
    p.add_argument("-n", type=int, default=30)
    p = sub.add_parser("bib", help="BibTeX for papers (ids, --project, or all)"); p.add_argument("ids", nargs="*")
    p = sub.add_parser("thought"); p.add_argument("text")
    p = sub.add_parser("thoughts"); p.add_argument("--all", action="store_true", help="include handled ones")
    a = ap.parse_args()
    name = a.cmd.replace("-", "_")
    try:
        if name in ("show", "set", "note") and LIB_ID(a.id):
            return lib_route(a)
        if name in GLOBAL_CMDS or name == "project_set":
            return globals()["cmd_" + name](a)
        a.slug = resolve(a)
        globals()["cmd_" + name](a, load(a.slug))
    except (KeyError, ValueError, OSError, RuntimeError) as e:
        sys.exit(f"error: {str(e).strip(chr(39))}")


if __name__ == "__main__":
    main()
