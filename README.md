# brainboard

A small, calm, self-hosted board for when your head is too full. It holds your open items (with priorities
and % progress), the decisions waiting on you, a brain-dump inbox, and a notes thread on every item. AI agents
(Claude, Codex, …) use the same board through a CLI, so the board is the shared interface between you and them.

- **Zero dependencies.** Python 3.9+ standard library and a single HTML file.
- **One JSON file** holds the whole board. Writes are atomic and file-locked, and every change is logged.
- **Live.** Edits from agents show up in the browser within ~2 s.

## Run locally
```bash
python3 brainboard/server.py          # http://127.0.0.1:8765, creates data/board.json
```

## Run on a server
```bash
export BRAINBOARD_PASSWORD='long-random-string'        # required off localhost (HTTP Basic auth)
export BRAINBOARD_DATA=~/.local/share/brainboard/board.json
python3 brainboard/server.py --host 0.0.0.0 --port 2001 --no-open
```
`deploy/brainboard.service` is a systemd **user** unit (needs `loginctl enable-linger $USER`), and
`deploy/deploy.sh <ssh-host>` syncs the code and restarts it. Basic auth over plain HTTP is not encrypted;
put it behind HTTPS (Caddy or nginx) or a VPN if the board holds anything sensitive.

## Agents / CLI
```bash
python3 brainboard/cli.py summary | next | list | show ID | set ID k=v… | note ID "…" | add "title" | thought "…" | thoughts
```
Point it at a remote board with `BRAINBOARD_URL` and `BRAINBOARD_PASSWORD`, or with
`~/.config/brainboard/config.json` → `{"url": "...", "password": "...", "who": "claude"}`. See [AGENTS.md](AGENTS.md).

## Board file
`meta` (title, categories, people, sources) · `items[]` (id, title, category, priority P1–P3, status
todo/doing/decide/blocked/done/skip, progress, owner, location, anchor, issue, fix, sources, tags, notes[]) ·
`thoughts[]` · `activity[]`.
