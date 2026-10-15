# brainboard

A small, calm, self-hosted board for when your head is too full. It holds your **projects**, and inside each
one your open items (with priorities and % progress), the decisions waiting on you, a brain-dump inbox and a
notes thread on every item. AI agents (Claude Code, Codex, …) use the same board through a CLI and an
installable skill, so the board is the shared interface between you and them.

- **Zero dependencies.** Python 3.9+ standard library and a single HTML file.
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
python3 brainboard/cli.py -p thesis project-set color=#0e8f87 archived=yes
```
Which project? `-p`, then `$BRAINBOARD_PROJECT`, then a `.brainboard` file (one line, the slug) in the cwd or a
parent, then the config default (`cli.py use SLUG`), then the only active project. Remote boards: `BRAINBOARD_URL`
+ `BRAINBOARD_PASSWORD`, or `cli.py config --url …`. See [AGENTS.md](AGENTS.md).

## Data
`<home>/projects/<slug>.json` = `meta` (title, description, icon, color, categories, people, sources, archived,
rev) · `items[]` (id, title, category, priority P1–P3, status todo/doing/decide/blocked/done/skip, progress,
owner, location, anchor, issue, fix, sources, tags, notes[]) · `thoughts[]` · `activity[]`.

HTTP API: `GET /api/projects`, `POST /api/projects` (`create`/`delete`), `GET /api/p/<slug>/state|version`,
`POST /api/p/<slug>/op`.
