# brainboard

A small, calm, self-hosted board for when your head is too full. It holds your **projects**, and inside each
one your open items (with priorities and % progress), the decisions waiting on you, the side **experiments**
you're running (setup, results, outcome), a brain-dump inbox and a notes thread on every item and experiment.
Next to the projects sits a **paper library** shared by all of them: PDFs with extracted full text, metadata
looked up on arXiv/Semantic Scholar/Crossref, BibTeX in and out, and a structured *card* per paper that agents
write after reading it, so the library can be used as context for brainstorming. A **topic map** places the papers
in their field (a tree of research directions, each with its position in the literature and open problems), so
you and an agent can dive into one direction with all its papers loaded (`topic-context`). Ideas that come out of it live
next to the papers (pitch, open questions, papers they build on, raw → promising → pursuing) and can be turned into
items or experiments in a project. AI agents (Claude Code, Codex, …) use the same board through a CLI and an
installable skill, so the board is the shared interface between you and them.

- **Zero dependencies.** Python 3.9+ standard library and a single HTML file (PDF text extraction uses
  poppler's `pdftotext`/`pdfinfo` if installed: `brew install poppler` / `apt install poppler-utils`).
- **One JSON file per project.** Writes are atomic and file-locked, and every change is logged.
- **Live.** Edits from agents show up in the browser within ~2 s.

## Run locally
```bash
python3 brainboard/server.py          # http://127.0.0.1:8765, data in ./data/projects/
```
Or double-click `start.command` on macOS. Create your first project from the home page.

## Run on a server
```bash
export BRAINBOARD_PASSWORD='long-random-string'        # required off localhost (HTTP Basic auth)
export BRAINBOARD_HOME=~/.local/share/brainboard       # projects/<slug>.json live here
python3 brainboard/server.py --host 0.0.0.0 --port 2001 --no-open
```
`deploy/brainboard.service` is a systemd **user** unit (needs `loginctl enable-linger $USER`), and
`deploy/deploy.sh <ssh-host>` syncs the code and restarts it. Basic auth over plain HTTP is not encrypted;
put it behind HTTPS (Caddy or nginx) or a VPN if the board holds anything sensitive.

Upgrading from the single-board version: keep `BRAINBOARD_DATA=…/board.json`. On first start the board
becomes the first project (in `projects/` next to it), and the old file is renamed `board.json.migrated`.
Deleted projects go to `trash/`.

## Voice input (optional)
Every note, thought and drawer text field gets a mic button: click to record, click again to stop, and the
transcript is appended to the field. The server forwards audio to any endpoint that speaks the OpenAI
transcription API (`POST …/v1/audio/transcriptions`, multipart `file` + `model`):
```bash
export BRAINBOARD_ASR_URL=http://127.0.0.1:8010/v1/audio/transcriptions   # asr/server.py below
# or: BRAINBOARD_ASR_URL=https://api.openai.com/v1/audio/transcriptions BRAINBOARD_ASR_KEY=sk-… BRAINBOARD_ASR_MODEL=whisper-1
```
`asr/server.py` serves [Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) locally (needs ffmpeg and a
GPU with ~8 GB in fp32; see its docstring for the install and `deploy/brainboard-asr.service` for systemd).

Browsers only allow the microphone on **HTTPS or localhost**. To get HTTPS without a proxy, set
`BRAINBOARD_TLS_PORT=2002 BRAINBOARD_TLS_CERT=cert.pem BRAINBOARD_TLS_KEY=key.pem`. The server then also
listens on HTTPS, and the plain-HTTP port keeps working for the CLI. A self-signed cert is fine: accept the
warning once. Over plain HTTP the mic button falls back to picking an audio file.

## Agents
Install the skill into your harness (re-run after pulling to update):
```bash
./install-skill.sh claude      # ~/.claude/skills/brainboard
./install-skill.sh codex       # ~/.codex/skills/brainboard  (or $CODEX_HOME)
./install-skill.sh agents      # ~/.agents/skills/brainboard (other Agent-Skills harnesses)
./install-skill.sh --dir PATH --who NAME
./install-skill.sh --snippet   # print an AGENTS.md / CLAUDE.md block for harnesses without skills
```
The skill bundles a copy of the CLI and a `bb` wrapper that signs changes with the harness name. Connect it once:
`~/.codex/skills/brainboard/scripts/bb config --url http://host:2001` (prompts for the password and writes
`~/.config/brainboard/config.json`, mode 600).

## CLI
```bash
python3 brainboard/cli.py projects                     # * = project commands will use
python3 brainboard/cli.py project-add "Thesis" --icon 🎓 --cats "Chapters,Experiments,Admin"
python3 brainboard/cli.py -p thesis inbox              # notes/thoughts waiting for you, per item
python3 brainboard/cli.py -p thesis summary | next | list | show ID | set ID k=v… | note ID "…" | add "title" | thought "…" | thoughts
python3 brainboard/cli.py -p thesis exp | exp-add "Rerun with seed 7" --problem "…" --where host:~/runs --related E09
python3 brainboard/cli.py -p thesis set X01 status=done outcome=worked results="…"
python3 brainboard/cli.py -p thesis project-set color=#0e8f87 archived=yes
python3 brainboard/cli.py paper-add arxiv:2401.12345 --projects thesis   # or --pdf file.pdf, --bib refs.bib
python3 brainboard/cli.py papers | show P03 | paper-text P03 --pages 2-4 | grep '"query graph" LLM'
python3 brainboard/cli.py paper-card P03 --file card.json | paper-refresh | -p thesis bib > refs.bib
python3 brainboard/cli.py -p thesis idea-add "Title" --pitch "…" --builds-on P17,P18 | ideas | idea-promote I03 --as experiment
python3 brainboard/cli.py topics --papers | topic-add "Title" --parent C03 --papers P20,P22 | topic-papers C05 P31 | topic-context C05
```
Which project? `-p`, then `$BRAINBOARD_PROJECT`, then a `.brainboard` file (one line, the slug) in the cwd or a
parent, then the config default (`cli.py use SLUG`), then the only active project. Remote boards: `BRAINBOARD_URL`
+ `BRAINBOARD_PASSWORD`, or `cli.py config --url …`. See [AGENTS.md](AGENTS.md).

## Data
`<home>/projects/<slug>.json` = `meta` (title, description, icon, color, categories, people, sources, archived,
rev) · `items[]` (id, title, category, priority P1–P3, status todo/doing/decide/blocked/done/skip, progress,
owner, location, anchor, issue, fix, sources, tags, notes[]) · `experiments[]` (id X…, title, status
planned/running/done/failed/dropped, outcome worked/inconclusive/didnt, owner, problem, setup, location,
results, related item ids, started, ended, notes[]) · `thoughts[]` · `activity[]`.

`<home>/library/library.json` = `papers[]` (id P…, title, short, authors, year, venue, doi, arxiv, url, abstract,
bibkey, tags, projects, reading to-read/skimmed/read/key, summary, card{tldr, problem, approach, contributions,
evaluation, limitations, gaps, questions, datasets[], metrics[]}, topics (first = main), notes[], pdf, pages) · `activity[]`; the PDFs
and their extracted text sit next to it in `pdf/` and `text/`. `ideas[]` (id I…, title, pitch, questions, builds_on paper ids,
projects, tags, topics, status raw/promising/pursuing/parked/dropped, promoted [{project, id}], notes[]). `topics[]` (id C…,
title, parent topic id, position, questions, notes[]).

HTTP API: `GET /api/library`, `POST /api/library/op|upload|import|bib|refresh`, `GET /api/library/pdf|text/<id>`,
`GET /api/library/grep|bib`, `GET /api/library/context/<C id>`; `GET /api/projects`, `POST /api/projects` (`create`/`delete`), `GET /api/p/<slug>/state|version`,
`POST /api/p/<slug>/op`.
