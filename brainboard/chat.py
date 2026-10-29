"""Chat with the paper library: an LLM agent that answers from the library through read-only tools.

The model is any OpenAI-compatible endpoint (a local router, OpenAI, vLLM, …):
  BRAINBOARD_LLM_URL     e.g. http://127.0.0.1:20128/v1   (chat is off when unset)
  BRAINBOARD_LLM_KEY     bearer token, if the endpoint needs one
  BRAINBOARD_LLM_MODEL   default model id; BRAINBOARD_LLM_MODELS = comma-separated ids to offer (default: all
                         models the endpoint lists as tool-capable)

Chats live in BRAINBOARD_HOME/library/chats/K01.json as OpenAI-style messages (plus `at`, `model`). The agent can
search (find, grep), read cards, page text and topic packs, and list ideas; it cannot change the library. Saving
an answer as a note is the user's click in the UI.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

import core
import library

LLM_URL = os.environ.get("BRAINBOARD_LLM_URL", "").rstrip("/")
LLM_KEY = os.environ.get("BRAINBOARD_LLM_KEY", "")
LLM_MODEL = os.environ.get("BRAINBOARD_LLM_MODEL", "")
LLM_MODELS = [m.strip() for m in os.environ.get("BRAINBOARD_LLM_MODELS", "").split(",") if m.strip()]
CHATS = os.path.join(library.LIB, "chats")
ID_RE = re.compile(r"^K\d{2,5}$")
MAX_STEPS = 10
TOOL_CAP = 24000        # characters of one tool result sent to the model
OLD_TOOL_CAP = 1500     # tool results from earlier turns are shortened when the history is replayed
_running = set()
_lock = threading.Lock()
_models = {"at": 0, "ids": []}


# ---------- storage ----------

def _path(cid):
    if not ID_RE.match(cid or ""):
        raise ValueError(f"bad chat id: {cid!r}")
    return os.path.join(CHATS, cid + ".json")


def load(cid):
    try:
        with open(_path(cid), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise KeyError(f"no such chat: {cid}")


def save(chat):
    os.makedirs(CHATS, exist_ok=True)
    chat["updated"] = core.now()
    tmp = _path(chat["id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(chat, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, _path(chat["id"]))


def list_chats():
    out = []
    if os.path.isdir(CHATS):
        for f in os.listdir(CHATS):
            if f.endswith(".json") and ID_RE.match(f[:-5]):
                try:
                    c = load(f[:-5])
                except (ValueError, OSError):
                    continue
                n = sum(1 for m in c["messages"] if m["role"] == "user")
                out.append({k: c.get(k) for k in ("id", "title", "scope", "model", "created", "updated")} | {"turns": n})
    return sorted(out, key=lambda c: c.get("updated") or "", reverse=True)


def create(scope=None, title="", who="me"):
    scope = _clean_scope(scope or {})
    with library.locked():
        os.makedirs(CHATS, exist_ok=True)
        n = max([int(f[1:-5]) for f in os.listdir(CHATS) if f.endswith(".json") and ID_RE.match(f[:-5])] or [0])
        chat = {"id": f"K{n + 1:02d}", "title": str(title or "").strip()[:120] or "New chat", "scope": scope,
                "model": "", "by": who, "created": core.now(), "updated": core.now(), "messages": []}
        save(chat)
    return chat


def update(cid, fields):
    chat = load(cid)
    if "title" in fields:
        chat["title"] = str(fields["title"]).strip()[:120] or chat["title"]
    if "scope" in fields:
        chat["scope"] = _clean_scope(fields["scope"])
    save(chat)
    return chat


def delete(cid):
    p = _path(cid)
    if not os.path.exists(p):
        raise KeyError(f"no such chat: {cid}")
    os.makedirs(os.path.join(library.TRASH), exist_ok=True)
    os.replace(p, os.path.join(library.TRASH, time.strftime("%Y%m%d-%H%M%S.") + cid + ".json"))


def _clean_scope(s):
    data = library.load()
    out = {}
    if s.get("topic"):
        out["topic"] = core._find(data.get("topics", []), str(s["topic"]).upper())["id"]
    if s.get("paper"):
        out["paper"] = core._find(data["papers"], str(s["paper"]).upper())["id"]
    return out


# ---------- the model endpoint ----------

def enabled():
    return bool(LLM_URL)


def _req(path, body=None, stream=False, timeout=300):
    h = {"Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json"}
    if LLM_KEY:
        h["Authorization"] = "Bearer " + LLM_KEY
    req = urllib.request.Request(LLM_URL + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"model endpoint said {e.code}: {msg}")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"can't reach the model endpoint {LLM_URL}: {getattr(e, 'reason', e)}")


def models():
    """Model ids to offer: BRAINBOARD_LLM_MODELS, else the endpoint's tool-capable models (cached 5 min)."""
    if LLM_MODELS:
        return LLM_MODELS
    if time.time() - _models["at"] < 300 and _models["ids"]:
        return _models["ids"]
    try:
        with _req("/models", timeout=10) as r:
            data = json.load(r).get("data", [])
        ids = [m["id"] for m in data if (m.get("capabilities") or {}).get("tools", True)]
    except (RuntimeError, ValueError, KeyError):
        ids = []
    _models.update(at=time.time(), ids=ids)
    return ids


def default_model():
    ms = models()
    return LLM_MODEL or (ms[0] if ms else "")


def _stream(model, messages, tools):
    """One streamed completion. Yields ("delta", text) and finally ("message", assistant message dict)."""
    body = {"model": model, "messages": messages, "stream": True}
    if tools:
        body["tools"] = tools
    content, calls, reasoning = [], {}, False
    with _req("/chat/completions", body, stream=True) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                ev = json.loads(payload)
            except ValueError:
                continue
            if ev.get("error"):
                raise RuntimeError(f"model error: {str(ev['error'])[:300]}")
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or ch.get("message") or {}
                if (d.get("reasoning_content") or d.get("reasoning")) and not reasoning:
                    reasoning = True
                    yield ("thinking", "")
                if d.get("content"):
                    content.append(d["content"])
                    yield ("delta", d["content"])
                for tc in d.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", len(calls)), {"id": "", "name": "", "args": ""})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["args"] += fn.get("arguments") or ""
    msg = {"role": "assistant", "content": "".join(content)}
    if calls:
        msg["tool_calls"] = [{"id": c["id"] or f"call_{i}", "type": "function",
                              "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                             for i, c in sorted(calls.items())]
    yield ("message", msg)


# ---------- tools (read-only) ----------

TOOLS = [
    {"name": "find", "description": "Rank the library's papers by relevance to a question (BM25 over cards and page text). "
     "Returns topics, each paper's tl;dr and page-cited passages. Use the field's vocabulary; rephrase and search again "
     "if results look off.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "the question or key terms"},
         "topic": {"type": "string", "description": "optional topic id (C06) to stay inside a direction"},
         "n": {"type": "integer", "description": "how many papers (default 8)"}}, "required": ["query"]}},
    {"name": "show_paper", "description": "A paper's metadata, full card (problem, approach, contributions, evaluation with "
     "numbers, limitations, gaps, questions, datasets, metrics), the user's summary, topics and notes.",
     "parameters": {"type": "object", "properties": {"id": {"type": "string", "description": "paper id, e.g. P12"}},
                    "required": ["id"]}},
    {"name": "read_pages", "description": "Extracted text of a paper's pages, with '--- page N ---' markers (max 5 pages per "
     "call). Use it to verify claims and quote exact numbers.",
     "parameters": {"type": "object", "properties": {"id": {"type": "string"},
                    "pages": {"type": "string", "description": "e.g. '7' or '6-8' or '3,9'"}}, "required": ["id", "pages"]}},
    {"name": "topic_context", "description": "Everything about one research direction: its position in the field, open "
     "problems, parent/neighbouring topics, every paper's card, linked ideas.",
     "parameters": {"type": "object", "properties": {"id": {"type": "string", "description": "topic id, e.g. C06"},
                    "brief": {"type": "boolean", "description": "tl;drs only (smaller)"}}, "required": ["id"]}},
    {"name": "grep", "description": "Exact-term full-text search (every term on the same page; quotes keep a phrase). "
     "Returns paper, page and snippet.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"},
                    "topic": {"type": "string", "description": "optional topic id"}}, "required": ["query"]}},
    {"name": "list_ideas", "description": "The user's research ideas (status, title, papers they build on), optionally "
     "for one topic.",
     "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}}},
]


def _cap(s, n=TOOL_CAP):
    return s if len(s) <= n else s[:n] + f"\n…[cut: {len(s) - n} more characters; ask for less, e.g. fewer pages]"


def paper_md(p, data):
    tby = {t["id"]: t["title"] for t in data.get("topics", [])}
    c = p.get("card") or {}
    out = [f"# {p['id']} {p.get('short') + ': ' if p.get('short') and not p['title'].startswith(p['short']) else ''}{p['title']}",
           f"{', '.join(p.get('authors') or [])} · {p.get('year') or '?'} · {p.get('venue') or '?'} · reading: {p.get('reading')}"
           + (f" · {p.get('pages')} pages" if p.get("pdf") else " · no PDF"),
           "Topics: " + (", ".join(f"{t} {tby.get(t, '')}" + (" (main)" if k == 0 else "") for k, t in enumerate(p.get("topics") or [])) or "none")]
    if p.get("summary"):
        out += ["\n## User's summary", p["summary"]]
    for k in library.CARD_TEXT:
        if c.get(k):
            out += [f"\n## {k}", c[k]]
    for k in library.CARD_LISTS:
        if c.get(k):
            out.append(f"**{k}:** {', '.join(c[k])}")
    if not c and p.get("abstract"):
        out += ["\n## abstract (no card yet)", p["abstract"]]
    for n in p.get("notes", []):
        out.append(f"\n[note {n['at'][:10]} {n['who']}] {n['text']}")
    return "\n".join(out)


def run_tool(name, args):
    data = library.load()
    if name == "find":
        return library.find_md(str(args.get("query", "")), str(args.get("topic") or ""), "", int(args.get("n") or 8))
    if name == "show_paper":
        return paper_md(core._find(data["papers"], str(args.get("id", "")).upper()), data)
    if name == "read_pages":
        pid = str(args.get("id", "")).upper()
        n = len(library.pages_of(pid))
        sel = library.parse_pages(str(args.get("pages") or "1"), n)[:5]
        return library.text(pid, ",".join(map(str, sel)))["text"] + (f"\n(paper has {n} pages)")
    if name == "topic_context":
        return library.topic_context(str(args.get("id", "")), bool(args.get("brief")))
    if name == "grep":
        hits = library.grep(str(args.get("query", "")), limit=25, topic=str(args.get("topic") or ""))
        return "\n".join(f"{h['id']} {h['short']} {'card/notes' if h['page'] == 'meta' else 'p.' + str(h['page'])}: …{h['snippet']}…"
                         for h in hits) or "no page has all of these terms"
    if name == "list_ideas":
        ideas = data.get("ideas", [])
        if args.get("topic"):
            tree = set(library.subtree(data, str(args["topic"]).upper()))
            ideas = [i for i in ideas if tree & set(i.get("topics", []))]
        return "\n".join(f"{i['id']} [{i['status']}] {i['title']}" + (f" (builds on {', '.join(i['builds_on'])})" if i.get("builds_on") else "")
                         + (f"\n  {library._first_line(i.get('pitch'))}" if i.get("pitch") else "") for i in ideas) or "no ideas"
    raise ValueError(f"unknown tool {name}")


def _tool_label(name, args):
    if name == "find":
        return f"find “{args.get('query', '')}”" + (f" in {args['topic']}" if args.get("topic") else "")
    if name == "read_pages":
        return f"read {args.get('id')} p.{args.get('pages')}"
    if name == "show_paper":
        return f"card of {args.get('id')}"
    if name == "topic_context":
        return f"topic {args.get('id')}" + (" (brief)" if args.get("brief") else "")
    if name == "grep":
        return f"grep {args.get('query', '')}" + (f" in {args['topic']}" if args.get("topic") else "")
    return name.replace("_", " ") + (f" {args['topic']}" if args.get("topic") else "")


# ---------- the agent ----------

SYSTEM = """You are a research assistant working from the user's own paper library in brainboard. The user is a
researcher; they read these papers and wrote (with agents) a critical *card* for each. Papers have ids like P12;
topics (C05) form a map of the field, each with a written position and open problems; ideas have ids like I03.

How to work:
- Ground every factual claim about a paper in the library: search with `find` (or `grep` for exact terms), then
  read cards (`show_paper`) and, for numbers or contested claims, the pages (`read_pages`). For a question about a
  whole direction, load `topic_context`. Prefer a few targeted tool calls over many.
- Cite as P12 (ShortName) p.7 — page numbers from passages or page text; P12 alone when citing the card.
- Separate what the papers say from your own reasoning. If the library has nothing on a point, say so
  ("none of the library's papers evaluates X"). You may add well-known papers from your own knowledge, but mark
  them clearly as not in the library.
- Be critical like the cards: evaluation leakage, tiny or reused test sets, re-implemented baselines, missing
  ablations, disagreements between papers. Connect findings to the topics' open problems and the user's ideas.
- Answer in Markdown, concise and structured; no preamble. Match the user's language."""


def _overview(data, scope):
    tby = {t["id"]: t for t in data.get("topics", [])}
    lines = ["## Topic map"]
    for t in data.get("topics", []):
        depth = len(library._ancestors(data, t["id"]))
        n = sum(1 for p in data["papers"] if set(p.get("topics", [])) & set(library.subtree(data, t["id"])))
        lines.append("  " * depth + f"- {t['id']} {t['title']} ({n})")
    lines.append(f"\n## Papers ({len(data['papers'])}; id, short name, year, main topic, title)")
    for p in data["papers"][:400]:
        lines.append(f"- {p['id']} {p.get('short') or ''} {p.get('year') or ''} [{(p.get('topics') or ['-'])[0]}] {p['title'][:110]}")
    if scope.get("topic") and scope["topic"] in tby:
        t = tby[scope["topic"]]
        lines += [f"\n## This conversation is about topic {t['id']} {t['title']}",
                  "Stay inside it (pass topic to find/grep) unless the user asks wider.", "", t.get("position") or "",
                  ("Open problems:\n" + t["questions"]) if t.get("questions") else ""]
    if scope.get("paper"):
        try:
            lines += ["\n## This conversation is about this paper", paper_md(core._find(data["papers"], scope["paper"]), data)]
        except KeyError:
            pass
    return "\n".join(lines)


def _api_messages(chat):
    """Stored history → what we send: drop our extra keys, shorten tool results from earlier turns."""
    msgs = chat["messages"]
    last_user = max([i for i, m in enumerate(msgs) if m["role"] == "user"] or [0])
    out = []
    for i, m in enumerate(msgs):
        x = {k: m[k] for k in ("role", "content", "tool_calls", "tool_call_id") if k in m}
        if m["role"] == "tool" and i < last_user:
            x["content"] = _cap(m["content"], OLD_TOOL_CAP)
        if m["role"] == "assistant" and not x.get("content") and not x.get("tool_calls"):
            continue
        out.append(x)
    return out


def send(cid, text, model="", who="me", emit=lambda kind, data: None):
    """Add the user's message and run the agent until it answers. emit(kind, data) streams progress:
    thinking, delta (text), tool (label), tool_done (label, chars), message (assistant message), error."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("empty message")
    if not enabled():
        raise RuntimeError("chat is not configured (set BRAINBOARD_LLM_URL)")
    with _lock:
        if cid in _running:
            raise ValueError("this chat is still answering")
        _running.add(cid)
    try:
        chat = load(cid)
        model = model or chat.get("model") or default_model()
        if not model:
            raise RuntimeError("no model available from the endpoint")
        chat["model"] = model
        if not chat["messages"] and chat["title"] == "New chat":
            chat["title"] = re.sub(r"\s+", " ", text)[:80]
        chat["messages"].append({"role": "user", "content": text, "who": who, "at": core.now()})
        save(chat)
        data = library.load()
        system = {"role": "system", "content": SYSTEM + "\n\n" + _overview(data, chat.get("scope") or {})}
        tools = [{"type": "function", "function": t} for t in TOOLS]
        for step in range(MAX_STEPS):
            final = step == MAX_STEPS - 1
            msg = None
            try:
                for kind, val in _stream(model, [system] + _api_messages(chat), [] if final else tools):
                    if kind == "message":
                        msg = val
                    else:
                        emit(kind, val)
            except (RuntimeError, OSError, ValueError) as e:   # keep the turn in the chat, marked as failed
                chat["messages"].append({"role": "assistant", "content": "", "error": str(e)[:500], "model": model, "at": core.now()})
                save(chat)
                raise RuntimeError(str(e)[:500])
            msg.update(at=core.now(), model=model)
            chat["messages"].append(msg)
            if not msg.get("tool_calls"):
                save(chat)
                emit("message", msg)
                return chat
            emit("step_end", "")
            for tc in msg["tool_calls"]:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except ValueError:
                    args = {}
                label = _tool_label(name, args)
                emit("tool", label)
                try:
                    out = _cap(run_tool(name, args))
                except (KeyError, ValueError, OSError) as e:
                    out = f"error: {str(e).strip(chr(39))}"
                chat["messages"].append({"role": "tool", "tool_call_id": tc["id"], "name": name, "label": label,
                                         "content": out, "at": core.now()})
                emit("tool_done", {"label": label, "chars": len(out), "error": out.startswith("error:")})
            save(chat)
        return chat
    finally:
        with _lock:
            _running.discard(cid)
