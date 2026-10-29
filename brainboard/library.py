"""The paper library: one collection shared by all projects.

Lives under BRAINBOARD_HOME/library:
  library.json   papers, ideas, topics + activity + rev
  pdf/P012.pdf   the original file
  text/P012.txt  pdftotext output; pages separated by form feeds (\\f) so agents can cite pages

Same rules as project boards: every writer goes through apply_op(), which takes the library lock,
re-reads the file, applies one operation, bumps `rev`, logs activity and writes atomically.
PDF text comes from poppler's `pdftotext`/`pdfinfo`; metadata from arXiv, Semantic Scholar and
Crossref (stdlib urllib, no keys needed).
"""
import fcntl
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime

import core

LIB = os.path.join(core.HOME, "library")
FILE = os.path.join(LIB, "library.json")
PDF_DIR = os.path.join(LIB, "pdf")
TEXT_DIR = os.path.join(LIB, "text")
TRASH = os.path.join(LIB, "trash")

READING = ["to-read", "skimmed", "read", "key"]
PAPER_FIELDS = {"title", "short", "authors", "year", "venue", "doi", "arxiv", "url", "abstract", "bibkey",
                "tags", "projects", "reading", "summary", "card", "topics"}
IDEA_STATUSES = ["raw", "promising", "pursuing", "parked", "dropped"]
IDEA_FIELDS = {"title", "pitch", "questions", "builds_on", "projects", "tags", "status", "topics"}
# topics (C01, C = cluster) map the field: a tree of research directions; a paper's first topic is its main one
TOPIC_FIELDS = {"title", "parent", "position", "questions"}
# card = the structured digest an agent writes after reading the paper (Markdown strings, lists for the last two)
CARD_TEXT = ["tldr", "problem", "approach", "contributions", "evaluation", "limitations", "gaps", "questions"]
CARD_LISTS = ["datasets", "metrics"]
CARD_KEYS = CARD_TEXT + CARD_LISTS + ["by", "at"]
MAX_PDF = 50 << 20
UA = "brainboard/1.0 (paper library; https://github.com/medmac01/brainboard)"
ARXIV_RE = re.compile(r"(?:arxiv[:/ ]\s*|abs/|pdf/)?(\d{4}\.\d{4,5})(v\d+)?", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)


# ---------- storage ----------

def empty():
    return {"meta": {"rev": 0, "created": core.now(), "updated": core.now(), "updated_by": "brainboard"},
            "papers": [], "ideas": [], "topics": [], "activity": []}


@contextmanager
def locked():
    os.makedirs(LIB, exist_ok=True)
    with open(os.path.join(LIB, ".lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load():
    if not os.path.exists(FILE):
        return empty()
    with open(FILE, encoding="utf-8") as fh:
        return json.load(fh)


def save(data):
    os.makedirs(LIB, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=LIB, prefix=".tmp.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, FILE)


def version():
    try:
        return f"{load()['meta'].get('rev', 0)}:{os.path.getmtime(FILE):.3f}"
    except (OSError, ValueError, KeyError):
        return "0"


def pdf_path(pid):
    return os.path.join(PDF_DIR, _safe_id(pid) + ".pdf")


def text_path(pid):
    return os.path.join(TEXT_DIR, _safe_id(pid) + ".txt")


def _safe_id(pid):
    if not re.match(r"^[PI]\d{2,5}$", pid or ""):
        raise ValueError(f"bad id: {pid!r}")
    return pid


# ---------- cleaning ----------

def norm_title(t):
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _clean_paper(fields):
    bad = set(fields) - PAPER_FIELDS
    if bad:
        raise ValueError(f"unknown paper field(s): {', '.join(sorted(bad))}")
    f = dict(fields)
    for k in ("authors", "tags", "projects", "topics"):
        if k in f:
            f[k] = core._as_list(f[k]) if k != "authors" else _authors(f[k])
    for s in f.get("projects", []):
        if not core.SLUG_RE.match(s):
            raise ValueError(f"bad project slug: {s!r}")
    if "reading" in f and f["reading"] not in READING:
        raise ValueError(f"reading must be one of {READING}")
    if "year" in f:
        f["year"] = int(f["year"]) if str(f["year"]).strip().isdigit() else None
    if "arxiv" in f and f["arxiv"]:
        m = ARXIV_RE.search(str(f["arxiv"]))
        if not m:
            raise ValueError(f"not an arXiv id: {f['arxiv']!r}")
        f["arxiv"] = m.group(1)
    if "doi" in f and f["doi"]:
        f["doi"] = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", str(f["doi"]).strip(), flags=re.I)
    if "card" in f:
        if not isinstance(f["card"], dict):
            raise ValueError("card must be an object")
        bad = set(f["card"]) - set(CARD_KEYS)
        if bad:
            raise ValueError(f"unknown card field(s): {', '.join(sorted(bad))} (allowed: {', '.join(CARD_KEYS)})")
        f["card"] = {k: (core._as_list(v) if k in CARD_LISTS else str(v).strip()) for k, v in f["card"].items()}
    for k in ("title", "short", "venue", "url", "abstract", "bibkey", "summary"):
        if k in f:
            f[k] = str(f[k] or "").strip()
    for k in ("title", "short", "venue"):  # publisher markup (<scp>, <i>) and wrapped lines
        if k in f:
            f[k] = html.unescape(re.sub(r"\s+", " ", re.sub(r"</?[a-zA-Z:]+[^>]*>", "", f[k])).strip())
    return f


def _authors(v):
    if isinstance(v, str):
        v = re.split(r"\s*;\s*|\s+and\s+|\n", v) if (";" in v or " and " in v or "\n" in v) else v.split(",")
    return [re.sub(r"\s+", " ", str(a)).strip() for a in v if str(a).strip()]


def _check_topics(ids, data):
    ids = [x.upper() for x in core._as_list(ids)]
    known = {t["id"] for t in data.get("topics", [])}
    missing = [x for x in ids if x not in known]
    if missing:
        raise ValueError(f"unknown topic id(s): {', '.join(missing)} (see bb topics)")
    return list(dict.fromkeys(ids))


def _clean_topic(fields, data, tid=None):
    bad = set(fields) - TOPIC_FIELDS
    if bad:
        raise ValueError(f"unknown topic field(s): {', '.join(sorted(bad))}")
    f = {k: str(v or "").strip() for k, v in fields.items()}
    if "title" in f and not f["title"]:
        raise ValueError("a topic needs a title")
    if f.get("parent"):
        f["parent"] = f["parent"].upper()
        core._find(data.get("topics", []), f["parent"])
        if tid and (f["parent"] == tid or tid in _ancestors(data, f["parent"])):
            raise ValueError(f"a topic can't sit inside itself ({f['parent']} is {tid} or below it)")
    return f


def _ancestors(data, tid):
    """Ids from the topic's parent up to the root."""
    out, by = [], {t["id"]: t for t in data.get("topics", [])}
    while tid in by and by[tid].get("parent") and by[tid]["parent"] not in out:
        tid = by[tid]["parent"]
        out.append(tid)
    return out


def subtree(data, tid):
    """The topic and every topic below it."""
    out = [tid]
    for t in data.get("topics", []):
        if tid in _ancestors(data, t["id"]):
            out.append(t["id"])
    return out


def _clean_idea(fields, data):
    bad = set(fields) - IDEA_FIELDS
    if bad:
        raise ValueError(f"unknown idea field(s): {', '.join(sorted(bad))}")
    f = dict(fields)
    if "status" in f and f["status"] not in IDEA_STATUSES:
        raise ValueError(f"idea status must be one of {IDEA_STATUSES}")
    for k in ("builds_on", "projects", "tags"):
        if k in f:
            f[k] = core._as_list(f[k])
    if "builds_on" in f:
        f["builds_on"] = [x.upper() for x in f["builds_on"]]
        known = {p["id"] for p in data["papers"]}
        missing = [x for x in f["builds_on"] if x not in known]
        if missing:
            raise ValueError(f"unknown paper id(s): {', '.join(missing)}")
    for s_ in f.get("projects", []):
        if not core.SLUG_RE.match(s_):
            raise ValueError(f"bad project slug: {s_!r}")
    if "topics" in f:
        f["topics"] = _check_topics(f["topics"], data)
    for k in ("title", "pitch", "questions"):
        if k in f:
            f[k] = str(f[k] or "").strip()
    if "title" in f and not f["title"]:
        raise ValueError("an idea needs a title")
    return f


def new_paper(pid):
    return {"id": pid, "title": "", "short": "", "authors": [], "year": None, "venue": "", "doi": "", "arxiv": "",
            "url": "", "abstract": "", "bibkey": "", "tags": [], "projects": [], "reading": "to-read",
            "summary": "", "card": {}, "topics": [], "notes": [], "pdf": False, "pages": 0, "sha256": "", "source": "",
            "added": core.now(), "updated": core.now()}


def find_dup(data, f, sha=""):
    """An existing paper with the same PDF, arXiv id, DOI or title, else None."""
    t = norm_title(f.get("title"))
    for p in data["papers"]:
        if (sha and p.get("sha256") == sha) or (f.get("arxiv") and p.get("arxiv") == f["arxiv"]) \
                or (f.get("doi") and p.get("doi", "").lower() == f["doi"].lower()) \
                or (t and len(t) > 20 and norm_title(p.get("title")) == t):
            return p
    return None


# ---------- operations ----------

def apply_op(op, who="me"):
    """Apply one operation to the library. Returns the new full state (op result id in state['last'])."""
    kind = op.get("op")
    with locked():
        data = load()
        papers = data["papers"]
        last = op.get("id")
        if kind == "paper_add":
            f = _clean_paper(dict(op.get("fields") or {}))
            if not f.get("title"):
                raise ValueError("a paper needs a title")
            if "topics" in f:
                f["topics"] = _check_topics(f["topics"], data)
            dup = find_dup(data, f, op.get("sha256", ""))
            if dup:
                raise ValueError(f"already in the library as {dup['id']}: {dup['title']}")
            p = new_paper(core._next_id(papers, "P"))
            p.update(f)
            p["source"] = str(op.get("source", ""))[:200]
            p["bibkey"] = p["bibkey"] or _bibkey(p, papers)
            papers.append(p)
            last = p["id"]
            _log(data, who, f"added paper “{p['title'][:90]}”", p["id"])
        elif kind == "paper_update":
            p = core._find(papers, op["id"])
            f = _clean_paper(dict(op["fields"]))
            if "topics" in f:
                f["topics"] = _check_topics(f["topics"], data)
            if "card" in f:  # merge: empty values remove a card field
                card = dict(p.get("card") or {})
                card.update(f["card"])
                card = {k: v for k, v in card.items() if v}
                if any(k in f["card"] for k in CARD_TEXT + CARD_LISTS):
                    card["by"], card["at"] = f["card"].get("by") or who, core.now()
                f["card"] = card
            changed = {k: v for k, v in f.items() if p.get(k) != v}
            if not changed:
                return _state(data, last)
            p.update(changed)
            p["updated"] = core.now()
            _log(data, who, core._describe({k: v for k, v in changed.items() if k != "card"})
                 + (", wrote card" if "card" in changed else ""), p["id"])
        elif kind == "paper_files":  # internal: set by attach_pdf after the files are written
            p = core._find(papers, op["id"])
            p.update({k: op["fields"][k] for k in ("pdf", "pages", "sha256", "chars") if k in op["fields"]})
            p["updated"] = core.now()
            _log(data, who, f"attached PDF ({p.get('pages', 0)} pages)", p["id"])
        elif kind == "paper_delete":
            p = core._find(papers, op["id"])
            papers.remove(p)
            os.makedirs(TRASH, exist_ok=True)
            stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
            for src in (pdf_path(p["id"]), text_path(p["id"])):
                if os.path.exists(src):
                    os.replace(src, os.path.join(TRASH, f"{stamp}.{os.path.basename(src)}"))
            with open(os.path.join(TRASH, f"{stamp}.{p['id']}.json"), "w", encoding="utf-8") as fh:
                json.dump(p, fh, indent=1, ensure_ascii=False)
            _log(data, who, f"deleted paper “{p['title'][:90]}”", p["id"])
        elif kind == "idea_add":
            ideas = data.setdefault("ideas", [])
            f = _clean_idea(dict(op.get("fields") or {}), data)
            if not f.get("title"):
                raise ValueError("an idea needs a title")
            idea = {"id": core._next_id(ideas, "I"), "title": "", "pitch": "", "questions": "", "builds_on": [],
                    "projects": [], "tags": [], "status": "raw", "promoted": [], "notes": [], "by": who,
                    "added": core.now(), "updated": core.now()}
            idea.update(f)
            ideas.append(idea)
            last = idea["id"]
            _log(data, who, f"new idea “{idea['title'][:90]}”", idea["id"])
        elif kind == "idea_update":
            idea = core._find(data.setdefault("ideas", []), op["id"])
            f = _clean_idea(dict(op["fields"]), data)
            changed = {k: v for k, v in f.items() if idea.get(k) != v}
            if not changed:
                return _state(data, last)
            idea.update(changed)
            idea["updated"] = core.now()
            _log(data, who, core._describe(changed), idea["id"])
        elif kind == "idea_delete":
            ideas = data.setdefault("ideas", [])
            idea = core._find(ideas, op["id"])
            ideas.remove(idea)
            _log(data, who, f"deleted idea “{idea['title'][:90]}”", idea["id"])
        elif kind == "idea_promote":  # turn an idea into an item or experiment in a project
            idea = core._find(data.setdefault("ideas", []), op["id"])
            slug, as_ = str(op.get("project", "")), op.get("as", "item")
            if as_ not in ("item", "experiment"):
                raise ValueError("promote as 'item' or 'experiment'")
            refs = ", ".join(idea["builds_on"])
            origin = f"From idea {idea['id']} in the library" + (f" (builds on {refs})" if refs else "") + "."
            text = "\n\n".join(x for x in (idea["pitch"], ("**Open questions**\n" + idea["questions"]) if idea["questions"] else "", origin) if x)
            if as_ == "experiment":
                st = core.apply_op(slug, {"op": "add", "coll": "experiments",
                                          "fields": {"title": idea["title"], "problem": text, "tags": ["idea:" + idea["id"]]}}, who)
                new_id = st["experiments"][-1]["id"]
            else:
                st = core.apply_op(slug, {"op": "add", "fields": {"title": idea["title"], "issue": text,
                                                                  "kind": "idea", "tags": ["idea:" + idea["id"]]}}, who)
                new_id = st["items"][-1]["id"]
            idea.setdefault("promoted", []).append({"project": slug, "id": new_id, "at": core.now()})
            if slug not in idea["projects"]:
                idea["projects"].append(slug)
            if idea["status"] in ("raw", "promising"):
                idea["status"] = "pursuing"
            idea["updated"] = core.now()
            _log(data, who, f"promoted to {slug}/{new_id}", idea["id"])
        elif kind == "topic_add":
            topics = data.setdefault("topics", [])
            f = _clean_topic(dict(op.get("fields") or {}), data)
            if not f.get("title"):
                raise ValueError("a topic needs a title")
            t = {"id": core._next_id(topics, "C"), "title": "", "parent": "", "position": "", "questions": "",
                 "notes": [], "by": who, "added": core.now(), "updated": core.now()}
            t.update(f)
            topics.append(t)
            last = t["id"]
            _place(data, t["id"], op.get("papers") or [], [], op.get("main"))
            _log(data, who, f"new topic “{t['title'][:90]}”", t["id"])
        elif kind == "topic_update":
            t = core._find(data.setdefault("topics", []), op["id"])
            f = _clean_topic(dict(op["fields"]), data, t["id"])
            changed = {k: v for k, v in f.items() if t.get(k) != v}
            if not changed:
                return _state(data, last)
            t.update(changed)
            t["updated"] = core.now()
            _log(data, who, core._describe(changed), t["id"])
        elif kind == "topic_papers":  # put papers in a topic or take them out
            t = core._find(data.setdefault("topics", []), op["id"])
            add, remove = _place(data, t["id"], op.get("add") or [], op.get("remove") or [], op.get("main"))
            if not add and not remove:
                return _state(data, last)
            _log(data, who, ", ".join(x for x in (add and "added " + ", ".join(add), remove and "removed " + ", ".join(remove)) if x), t["id"])
        elif kind == "topic_delete":  # subtopics move up to the parent; papers and ideas just lose the topic
            topics = data.setdefault("topics", [])
            t = core._find(topics, op["id"])
            for x in topics:
                if x.get("parent") == t["id"]:
                    x["parent"] = t.get("parent", "")
            for x in papers + data.get("ideas", []):
                if t["id"] in x.get("topics", []):
                    x["topics"].remove(t["id"])
            topics.remove(t)
            _log(data, who, f"deleted topic “{t['title'][:90]}”", t["id"])
        elif kind == "note":
            p = core._find(papers + data.setdefault("ideas", []) + data.setdefault("topics", []), op["id"])
            text = op["text"].strip()
            if not text:
                raise ValueError("empty note")
            p.setdefault("notes", []).append({"who": who, "at": core.now(), "text": text})
            p["updated"] = core.now()
            _log(data, who, "commented: " + (text[:80] + "…" if len(text) > 80 else text), p["id"])
        else:
            raise ValueError(f"unknown library op: {kind}")
        data["meta"]["rev"] = data["meta"].get("rev", 0) + 1
        data["meta"]["updated"] = core.now()
        data["meta"]["updated_by"] = who
        save(data)
        return _state(data, last)


def _place(data, tid, add, remove, main=False):
    """Add/remove a topic on papers (by id). main=True puts it first (the paper's main topic). Returns the ids changed."""
    papers = data["papers"]
    add, remove = [x.upper() for x in core._as_list(add)], [x.upper() for x in core._as_list(remove)]
    for x in add + remove:
        core._find(papers, x)
    done_add, done_rm = [], []
    for p in papers:
        ts = p.setdefault("topics", [])
        if p["id"] in add and (tid not in ts or (main and ts[0] != tid)):
            if tid in ts:
                ts.remove(tid)
            ts.insert(0, tid) if main else ts.append(tid)
            done_add.append(p["id"])
        elif p["id"] in remove and tid in ts:
            ts.remove(tid)
            done_rm.append(p["id"])
        else:
            continue
        p["updated"] = core.now()
    return done_add, done_rm


def _state(data, last=None):
    data["last"] = last
    return data


def _log(data, who, text, item=None):
    core._log(data, who, text, item)


def get(pid):
    return core._find(load()["papers"], pid)


def get_idea(iid):
    return core._find(load().get("ideas", []), iid)


def topic_context(tid, brief=False):
    """Everything an agent needs to dig into one direction, as Markdown: where the topic sits (ancestors,
    siblings, subtopics), its position and open questions, the full card of every paper in it (main topic first),
    and the ideas linked to it or built on its papers."""
    data = load()
    topics = data.get("topics", [])
    by = {t["id"]: t for t in topics}
    t = core._find(topics, tid.upper())
    tree = subtree(data, t["id"])
    out = [f"# {t['id']} {t['title']}"]
    path = [by[x] for x in reversed(_ancestors(data, t["id"]))]
    if path:
        out.append("Inside: " + " › ".join(f"{x['id']} {x['title']}" for x in path))
    for x in path:
        if x.get("position"):
            out += [f"\n## Parent {x['id']} {x['title']}", x["position"]]
    for k, h in (("position", "Position in the field"), ("questions", "Open questions")):
        if t.get(k):
            out += [f"\n## {h}", t[k]]
    for t2 in t.get("notes", []):
        out.append(f"\n[{t2['at'][:16]} {t2['who']}] {t2['text']}")
    kids = [x for x in topics if x.get("parent") == t["id"]]
    sibs = [x for x in topics if x.get("parent", "") == t.get("parent", "") and x["id"] != t["id"]]
    count = lambda x: sum(1 for p in data["papers"] if set(p.get("topics", [])) & set(subtree(data, x)))
    for label, xs in (("Subtopics", kids), ("Neighbouring topics", sibs)):
        if xs:
            out.append(f"\n## {label}")
            out += [f"- {x['id']} {x['title']} ({count(x['id'])} papers)" + (f": {_first_line(x.get('position'))}" if x.get("position") else "") for x in xs]
    ps = [p for p in data["papers"] if set(p.get("topics", [])) & set(tree)]
    ps.sort(key=lambda p: (p["topics"][0] not in tree, p.get("year") or 0))
    out.append(f"\n## Papers ({len(ps)}; main topic here first, then oldest first)")
    for p in ps:
        c = p.get("card") or {}
        where = "main" if p["topics"][0] in tree else "also " + ", ".join(p["topics"][:1])
        short = f"{p['short']}: " if p.get("short") and not p["title"].startswith(p["short"]) else ""
        head = f"\n### {p['id']} {short}{p['title']} ({_authors_short(p)} {p.get('year') or ''}, {p.get('venue') or '?'}) [{where}; {p.get('reading')}]"
        out.append(head)
        if c.get("tldr"):
            out.append(c["tldr"])
        elif p.get("abstract"):
            out.append("(no card) " + p["abstract"])
        if p.get("summary"):
            out += ["**User's summary:** " + p["summary"]]
        if not brief:
            for k in CARD_TEXT[1:]:
                if c.get(k):
                    out += [f"**{k}:** {c[k]}"]
            for k in CARD_LISTS:
                if c.get(k):
                    out += [f"**{k}:** {', '.join(c[k])}"]
            for n in p.get("notes", []):
                out.append(f"[note {n['at'][:10]} {n['who']}] {n['text']}")
    pids = {p["id"] for p in ps}
    filed = [i for i in data.get("ideas", []) if set(i.get("topics", [])) & set(tree)]
    citing = [i for i in data.get("ideas", []) if i not in filed and set(i.get("builds_on", [])) & pids]
    line = lambda i: f"- {i['id']} [{i['status']}] {i['title']}" + (f" (builds on {', '.join(i['builds_on'])})" if i.get("builds_on") else "")
    if filed:
        out.append("\n## Ideas in this topic")
        for i in filed:
            out.append(line(i))
            if not brief and i.get("pitch"):
                out.append("  " + i["pitch"].replace("\n", "\n  "))
    if citing:
        out.append("\n## Other ideas that build on these papers")
        out += [line(i) for i in citing]
    return "\n".join(out) + "\n"


def _first_line(s):
    s = (s or "").strip().split("\n")[0]
    return s[:160] + ("…" if len(s) > 160 else "")


def _authors_short(p):
    a = p.get("authors") or []
    return (a[0].split()[-1] + (" et al." if len(a) > 1 else "")) if a else "?"


# ---------- PDFs ----------

def _run(cmd, data=None):
    return subprocess.run(cmd, input=data, capture_output=True, timeout=120).stdout


def extract(pdf_file):
    """(text with \\f page breaks, page count, pdfinfo dict) via poppler."""
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext not found (install poppler-utils / brew install poppler)")
    text = _run(["pdftotext", "-enc", "UTF-8", pdf_file, "-"]).decode("utf-8", "replace")
    # ligatures (ﬁ → fi) and stray control bytes from broken font maps; keep \n and the \f page breaks
    text = re.sub(r"[\x00-\x08\x0b\x0e-\x1f\x7f]", " ", unicodedata.normalize("NFKC", text))
    info = {}
    for ln in _run(["pdfinfo", pdf_file]).decode("utf-8", "replace").splitlines():
        k, _, v = ln.partition(":")
        info[k.strip()] = v.strip()
    pages = int(info.get("Pages") or 0) or text.count("\f") or 1
    return text, pages, info


def attach_pdf(pid, raw, who="me"):
    """Store a PDF for an existing paper, extract its text. Returns the new state."""
    if not raw.startswith(b"%PDF"):
        raise ValueError("that file is not a PDF")
    if len(raw) > MAX_PDF:
        raise ValueError("PDF too large (50 MB max)")
    get(pid)
    os.makedirs(PDF_DIR, exist_ok=True)
    os.makedirs(TEXT_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=PDF_DIR, prefix=".tmp.", suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    text, pages, _ = extract(tmp)
    os.replace(tmp, pdf_path(pid))
    with open(text_path(pid), "w", encoding="utf-8") as fh:
        fh.write(text)
    return apply_op({"op": "paper_files", "id": pid, "fields": {
        "pdf": True, "pages": pages, "sha256": hashlib.sha256(raw).hexdigest(), "chars": len(text)}}, who)


def add_pdf(raw, filename="", who="me", fields=None):
    """Add a paper from a PDF: guess metadata (arXiv/DOI in the file, Zotero-style name, pdfinfo, first page),
    enrich it online, store the file. Returns (state, id, existed)."""
    if not raw.startswith(b"%PDF"):
        raise ValueError("that file is not a PDF")
    sha = hashlib.sha256(raw).hexdigest()
    data = load()
    for p in data["papers"]:
        if p.get("sha256") == sha:
            return _state(data, p["id"]), p["id"], True
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tf:
        tf.write(raw)
        tf.flush()
        text, _, info = extract(tf.name)
    guess = guess_from_pdf(text, info, filename)
    guess.update({k: v for k, v in (fields or {}).items() if v})
    meta = lookup(guess)
    f = {**guess, **{k: v for k, v in meta.items() if v}}
    if guess.get("title") and not meta:
        f["title"] = guess["title"]
    dup = find_dup(data, f)
    if dup:
        if not dup.get("pdf"):
            return attach_pdf(dup["id"], raw, who), dup["id"], True
        return _state(data, dup["id"]), dup["id"], True
    src = f.pop("_source", "") or ("pdf " + os.path.basename(filename) if filename else "pdf upload")
    st = apply_op({"op": "paper_add", "fields": _paper_fields(f), "sha256": sha, "source": src}, who)
    pid = st["last"]
    return attach_pdf(pid, raw, who), pid, False


def _paper_fields(f):
    return {k: v for k, v in f.items() if k in PAPER_FIELDS and v not in (None, "", [])}


ZOTERO_RE = re.compile(r"^(?P<auth>.+?) - (?P<year>(19|20)\d\d) - (?P<title>.+)$")


def guess_from_pdf(text, info, filename=""):
    """Best-effort metadata from the PDF itself, before any lookup."""
    g = {}
    head = text[:6000]
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    m = re.match(r"^(\d{4}\.\d{4,5})(v\d+)?$", stem) or re.search(r"arXiv:(\d{4}\.\d{4,5})(v\d+)?", head)
    if m:
        g["arxiv"] = m.group(1)
    m = DOI_RE.search(head)
    if m:
        g["doi"] = m.group(1).rstrip(".,;)")
    m = ZOTERO_RE.match(stem)
    if m:
        g["title"] = m.group("title").strip()
        g["year"] = int(m.group("year"))
        g["_first_author"] = m.group("auth").split(" et al")[0].split(" and ")[0].strip()
    elif stem and not g.get("arxiv") and not re.match(r"^[\d.\-_s]+main$|^\d", stem):
        g["title"] = re.sub(r"[_]+", " ", stem).strip()
    t = (info.get("Title") or "").strip()
    if len(t) > 15 and not re.search(r"\.(docx?|tex|dvi|pdf)$|^untitled|^microsoft word", t, re.I):
        g["title"] = t if not g.get("title") or len(t) > len(g["title"]) else g["title"]
    if not g.get("title"):  # first substantial lines of page 1
        lines = [ln.strip() for ln in head.split("\f")[0].splitlines() if len(ln.strip()) > 3]
        cand = [ln for ln in lines[:12] if not re.search(r"arxiv|journal|vol\.|doi|http|©|preprint|received", ln, re.I)]
        if cand:
            g["title"] = " ".join(cand[:2]) if len(cand[0]) < 40 and len(cand) > 1 else cand[0]
    return g


# ---------- metadata lookup ----------

_last_s2 = [0.0]


def _get(url, accept="application/json", timeout=25):
    if "semanticscholar" in url:  # keyless S2 allows about one request per second
        time.sleep(max(0, _last_s2[0] + 1.2 - time.time()))
        _last_s2[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            if e.code == 404:
                return None
            raise RuntimeError(f"{urllib.parse.urlsplit(url).netloc} said {e.code}")
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"can't reach {urllib.parse.urlsplit(url).netloc}: {getattr(e, 'reason', e)}")
    return None


def arxiv_meta(aid):
    raw = _get("https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(aid), "application/atom+xml")
    if not raw:
        return {}
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    e = ET.fromstring(raw).find("a:entry", ns)
    if e is None or e.find("a:title", ns) is None:
        return {}
    sp = lambda s: re.sub(r"\s+", " ", s or "").strip()
    out = {"title": sp(e.findtext("a:title", "", ns)), "abstract": sp(e.findtext("a:summary", "", ns)),
           "authors": [sp(a.findtext("a:name", "", ns)) for a in e.findall("a:author", ns)],
           "year": int((e.findtext("a:published", "", ns) or "0")[:4]) or None, "arxiv": aid,
           "url": f"https://arxiv.org/abs/{aid}", "venue": "arXiv"}
    doi = e.findtext("x:doi", "", ns)
    jr = sp(e.findtext("x:journal_ref", "", ns))
    if doi:
        out["doi"] = doi
    if jr:
        out["venue"] = jr
    return out


S2_FIELDS = "title,authors,year,venue,externalIds,abstract,publicationVenue,url,journal"


def s2_meta(ref):
    """Semantic Scholar by 'arXiv:…' or 'DOI:…'."""
    raw = _get(f"https://api.semanticscholar.org/graph/v1/paper/{urllib.parse.quote(ref, safe=':/')}?fields={S2_FIELDS}")
    return _s2(json.loads(raw)) if raw else {}


def s2_title(title, first_author=None):
    """Semantic Scholar by title: exact match first, then a normal search (handles truncated titles)."""
    api = "https://api.semanticscholar.org/graph/v1/paper/search"
    q = urllib.parse.quote(title[:300])
    for url in (f"{api}/match?fields={S2_FIELDS}&query={q}", f"{api}?limit=5&fields={S2_FIELDS}&query={q}"):
        raw = _get(url)
        for hit in (json.loads(raw).get("data") or []) if raw else []:
            out = _s2(hit)
            if _same_title(out["title"], title, first_author, out["authors"]):
                return out
    return {}


def _s2(hit):
    ext = hit.get("externalIds") or {}
    pv = hit.get("publicationVenue") or {}
    out = {"title": hit.get("title") or "", "authors": [a["name"] for a in hit.get("authors") or []],
           "year": hit.get("year"), "venue": pv.get("name") or hit.get("venue") or "",
           "abstract": hit.get("abstract") or "", "doi": ext.get("DOI", ""), "arxiv": ext.get("ArXiv", ""),
           "url": hit.get("url") or ""}
    if out["doi"].lower().startswith("10.48550/"):
        out["doi"] = ""
    return out


def crossref_meta(doi):
    raw = _get("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/"))
    if not raw:
        return {}
    m = json.loads(raw)["message"]
    # latest of print/online/issued: Crossref's "issued" is sometimes the submission year (CODASPY '25 → 2024)
    years = [(m.get(k) or {}).get("date-parts", [[None]])[0][0] for k in ("published-print", "published-online", "issued")]
    date = [max([y for y in years if y] or [None])]
    abstract = re.sub(r"<[^>]+>", " ", m.get("abstract") or "")
    title = " ".join(m.get("title") or [])
    if m.get("subtitle") and m["subtitle"][0].lower() not in title.lower():
        title += ": " + m["subtitle"][0]
    return {"title": re.sub(r"<[^>]+>", "", title), "doi": m.get("DOI", doi),
            "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in m.get("author") or []],
            "year": date[0] if date else None, "venue": " ".join(m.get("container-title") or []),
            "abstract": re.sub(r"\s+", " ", abstract).strip(), "url": m.get("URL", "")}


def lookup(g):
    """Fill metadata online from what we know (arXiv id > DOI > title). Never raises; {} if nothing found."""
    out = {}
    try:
        if g.get("arxiv"):
            out = arxiv_meta(g["arxiv"])
            out["_source"] = "arXiv " + g["arxiv"]
        elif g.get("doi"):
            out = crossref_meta(g["doi"])
            out["_source"] = "Crossref " + g["doi"]
        if g.get("title") and not out.get("title"):
            s2 = s2_title(g["title"], g.get("_first_author"))
            if s2:
                out = s2
                out["_source"] = "Semantic Scholar title match"
        if out.get("title") and (out.get("venue", "arXiv") == "arXiv" or not out.get("abstract") or not out.get("doi")):
            ref = f"arXiv:{out['arxiv']}" if out.get("arxiv") else (f"DOI:{out['doi']}" if out.get("doi") else "")
            s2 = s2_meta(ref) if ref and not out["_source"].startswith("Semantic") else {}
            for k in ("doi", "abstract"):
                if s2.get(k) and not out.get(k):
                    out[k] = s2[k]
        if out.get("doi") and not out["_source"].startswith("Crossref"):
            cr = crossref_meta(out["doi"])   # the published version: venue and year
            for k in ("venue", "year"):
                if cr.get(k):
                    out[k] = cr[k]
    except (RuntimeError, ValueError, ET.ParseError):
        pass
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def _same_title(a, b, first_author=None, authors=None):
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return False
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    wa, wb = set(a.split()), set(b.split())
    overlap = len(wa & wb) / max(1, min(len(wa), len(wb)))
    if first_author and authors:
        if not any(first_author.lower().split()[-1] in x.lower() for x in authors):
            return False
    return overlap >= 0.8


def refresh(pid, who="me"):
    """Re-run the metadata lookup for a paper; fills only empty fields, and replaces an 'arXiv' venue
    once a published version is known. Returns (state, changed field names)."""
    p = get(pid)
    meta = lookup({k: p.get(k) for k in ("arxiv", "doi", "title")})
    f = {}
    for k, v in _paper_fields(meta).items():
        if k in ("title", "url"):
            continue
        if not p.get(k) or (k == "venue" and p[k] == "arXiv" and v != "arXiv") or (k == "year" and p.get("venue") == "arXiv" and meta.get("venue", "arXiv") != "arXiv"):
            f[k] = v
    if not f:
        return _state(load(), pid), []
    return apply_op({"op": "paper_update", "id": pid, "fields": f}, who), sorted(f)


def import_ref(ref, who="me", fields=None):
    """Add a paper from 'arxiv:2401.12345', an arXiv/DOI URL or a DOI; fetches the PDF when it's on arXiv.
    Returns (state, id, existed)."""
    ref = ref.strip()
    g = {}
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", ref, re.I) or re.match(r"^(?:arxiv:)?(\d{4}\.\d{4,5})(v\d+)?$", ref, re.I)
    if m:
        g["arxiv"] = m.group(1)
    else:
        m = DOI_RE.search(ref)
        if not m:
            raise ValueError("give an arXiv id (arxiv:2401.12345), an arXiv/DOI link or a DOI")
        g["doi"] = m.group(1).rstrip(".,;)")
        if g["doi"].lower().startswith("10.48550/arxiv."):
            g = {"arxiv": g["doi"][16:]}
    data = load()
    dup = find_dup(data, g)
    if dup:
        return _state(data, dup["id"]), dup["id"], True
    meta = lookup(g)
    if not meta.get("title"):
        raise ValueError(f"couldn't find metadata for {ref}")
    f = {**meta, **{k: v for k, v in (fields or {}).items() if v}}
    dup = find_dup(data, f)
    if dup:
        return _state(data, dup["id"]), dup["id"], True
    src = f.pop("_source", ref)
    st = apply_op({"op": "paper_add", "fields": _paper_fields(f), "source": src}, who)
    pid = st["last"]
    if f.get("arxiv"):
        try:
            raw = _get(f"https://arxiv.org/pdf/{f['arxiv']}", "application/pdf", timeout=90)
            if raw and raw.startswith(b"%PDF"):
                st = attach_pdf(pid, raw, who)
        except RuntimeError:
            pass
    return st, pid, False


# ---------- reading and searching ----------

def pages_of(pid):
    try:
        with open(text_path(pid), encoding="utf-8") as fh:
            return fh.read().split("\f")
    except FileNotFoundError:
        raise KeyError(f"{pid} has no PDF text")


def parse_pages(spec, n):
    """'3-5,8' -> [3,4,5,8] (1-based, clipped to n)."""
    out = []
    for part in (spec or "").split(","):
        a, dash, b = part.strip().partition("-")
        if a.isdigit():
            hi = int(b) if b.isdigit() else (n if dash else int(a))   # "3-" runs to the last page
            out += [p for p in range(int(a), hi + 1) if 1 <= p <= n]
    return out or list(range(1, n + 1))


def text(pid, pages=""):
    """Plain text of a paper with '--- page N ---' markers."""
    pg = pages_of(pid)
    while pg and not pg[-1].strip():
        pg.pop()
    sel = parse_pages(pages, len(pg))
    return {"id": pid, "pages": len(pg), "text": "\n".join(f"--- page {k} ---\n{pg[k - 1].strip()}" for k in sel)}


def _terms(q):
    return [t.lower() for t in re.findall(r'"([^"]+)"|(\S+)', q) for t in t if t]


def grep(q, ids=None, project="", limit=40, width=150, topic=""):
    """Search full text (per page) plus each paper's title/abstract/summary/card/notes.
    A page matches when it has every term; quoted phrases stay together. Ranked by hits."""
    terms = _terms(q)
    if not terms:
        raise ValueError("empty search")
    hits, data = [], load()
    tree = set(subtree(data, topic.upper())) if topic else None
    for p in data["papers"]:
        if ids and p["id"] not in ids or project and project not in p.get("projects", []) \
                or tree and not tree & set(p.get("topics", [])):
            continue
        meta = " ".join([p["title"], p.get("short", ""), p.get("abstract", ""), p.get("summary", ""),
                         json.dumps(p.get("card", {}), ensure_ascii=False), " ".join(n["text"] for n in p.get("notes", []))])
        chunks = [("meta", meta)]
        if p.get("pdf"):
            try:
                chunks += [(k + 1, pg) for k, pg in enumerate(pages_of(p["id"]))]
            except KeyError:
                pass
        for where, body in chunks:
            flat = re.sub(r"\s+", " ", body)
            low = flat.lower()
            if all(t in low for t in terms):
                i = low.find(terms[0])
                s = flat[max(0, i - width // 2): i + width].strip()
                hits.append({"id": p["id"], "short": p.get("short") or p["title"][:40], "page": where,
                             "count": sum(low.count(t) for t in terms), "snippet": s})
    hits.sort(key=lambda h: (-h["count"]))
    return hits[:limit]


# ---------- BibTeX ----------

def _bibkey(p, papers):
    last = (p["authors"][0].split()[-1] if p.get("authors") else "anon")
    last = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", last).encode("ascii", "ignore").decode().lower()) or "anon"
    word = next((w for w in norm_title(p.get("short") or p.get("title")).split()
                 if w not in ("a", "an", "the", "on", "of", "towards", "toward")), "paper")
    base = f"{last}{p.get('year') or ''}{word}"
    taken = {x.get("bibkey") for x in papers}
    key, n = base, 0
    while key in taken:
        n += 1
        key = base + "abcdefghijklmnopqrstuvwxyz"[n - 1]
    return key


def _bibesc(s):
    return str(s).replace("{", "\\{").replace("}", "\\}") if s else ""


def bibtex(papers):
    out = []
    for p in papers:
        arx = p.get("arxiv") and (not p.get("venue") or p["venue"].lower().startswith("arxiv"))
        typ = "misc" if arx else ("inproceedings" if re.search(r"proc|conf|symposium|workshop|ccs|usenix|ndss|s&p|sigsac|raid|acsac|esorics|asiaccs|www", p.get("venue", ""), re.I) else "article")
        f = [("title", "{" + _bibesc(p["title"]) + "}"), ("author", " and ".join(p.get("authors") or []))]
        if typ == "inproceedings":
            f.append(("booktitle", p.get("venue")))
        elif typ == "article":
            f.append(("journal", p.get("venue")))
        f += [("year", p.get("year")), ("doi", p.get("doi"))]
        if p.get("arxiv"):
            f += [("eprint", p["arxiv"]), ("archivePrefix", "arXiv")]
        f.append(("url", p.get("url")))
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in f if v)
        out.append(f"@{typ}{{{p.get('bibkey') or p['id']},\n{body}\n}}")
    return "\n\n".join(out) + "\n"


def parse_bib(src):
    """Minimal BibTeX reader: entries -> paper fields (title, authors, year, venue, doi, arxiv, url, abstract, bibkey)."""
    out = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", src):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        i, depth, j = m.end(), 1, m.end()
        while j < len(src) and depth:
            depth += {"{": 1, "}": -1}.get(src[j], 0)
            j += 1
        body = src[i:j - 1]
        f = {}
        for fm in re.finditer(r"(\w+)\s*=\s*", body):
            k, v_start = fm.group(1).lower(), fm.end()
            if v_start >= len(body):
                continue
            if body[v_start] == "{":
                d, e = 1, v_start + 1
                while e < len(body) and d:
                    d += {"{": 1, "}": -1}.get(body[e], 0)
                    e += 1
                v = body[v_start + 1:e - 1]
            elif body[v_start] == '"':
                e = body.find('"', v_start + 1)
                v = body[v_start + 1:e]
            else:
                v = re.match(r"[^,\n}]*", body[v_start:]).group(0)
            f.setdefault(k, re.sub(r"\s+", " ", v.replace("{", "").replace("}", "")).strip())
        p = {"title": f.get("title", ""), "bibkey": m.group(2),
             "authors": [a.strip() if "," not in a else " ".join(reversed([x.strip() for x in a.split(",", 1)]))
                         for a in re.split(r"\s+and\s+", f.get("author", "")) if a.strip()],
             "year": f.get("year", ""), "venue": f.get("journal") or f.get("booktitle") or f.get("publisher", ""),
             "doi": f.get("doi", ""), "url": f.get("url", ""), "abstract": f.get("abstract", "")}
        ep = f.get("eprint", "")
        if re.match(r"^\d{4}\.\d{4,5}", ep):
            p["arxiv"] = ep
        out.append({k: v for k, v in p.items() if v})
    return out


def import_bib(src, who="me", tags=None, projects=None):
    """Add every BibTeX entry not already in the library. Returns (state, added ids, skipped ids)."""
    added, skipped, st = [], [], load()
    for f in parse_bib(src):
        if not f.get("title"):
            continue
        dup = find_dup(st, _clean_paper(_paper_fields(f)))
        if dup:
            skipped.append(dup["id"])
            continue
        if tags:
            f["tags"] = tags
        if projects:
            f["projects"] = projects
        st = apply_op({"op": "paper_add", "fields": _paper_fields(f), "source": "BibTeX"}, who)
        added.append(st["last"])
    return _state(st, added[-1] if added else None), added, skipped
