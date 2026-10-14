# brainboard: protocol for agents

The board is the shared to-do and decision list between a person ("me") and their AI agents.
The person works in the browser; agents use `brainboard/cli.py`.

## Before you start
```bash
python3 brainboard/cli.py summary     # overall % and per-category progress
python3 brainboard/cli.py next        # needs-decision, then doing, then highest-priority todo
python3 brainboard/cli.py thoughts    # the person's brain dump; may contain requests for you
python3 brainboard/cli.py show E01    # full item incl. notes thread
```
Read any pinned/early thoughts: project-specific rules (e.g. changelog conventions) live on the board itself.

## While you work
- Claim the item: `set E05 status=doing owner=claude`. Sign with `--who <name>` or `BRAINBOARD_WHO`.
- Report progress honestly: `set E05 progress=50`. Progress 100 marks it done.
- Put findings, questions and what you changed (file:line) in notes: `note E05 "…"`.
- If it needs the person's call, set `status=decide` and lay out the options in a note. Don't decide for them.
- New issue? `add "title" --cat "<category>" --priority P1 --issue "…"`.

Statuses: `todo doing decide blocked done skip` · Priorities: `P1 P2 P3`.
Prefer the CLI to editing the JSON by hand: it locks the file, logs activity and bumps `rev`.
