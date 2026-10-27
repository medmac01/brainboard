# brainboard: protocol for agents

The board is the shared to-do and decision list between a person ("me") and their AI agents, split into
projects. The person works in the browser; agents use `brainboard/cli.py` (or the installed skill's `bb`
wrapper, see `./install-skill.sh --help`). The full agent instructions are in [skill/SKILL.md](skill/SKILL.md).

## Before you start
```bash
python3 brainboard/cli.py projects            # pick the project; * marks the current default
python3 brainboard/cli.py -p SLUG inbox       # notes by others after your last note, per item + open thoughts
python3 brainboard/cli.py -p SLUG next        # needs-decision, then doing, then highest-priority todo
python3 brainboard/cli.py -p SLUG show E01    # full item incl. notes thread
```
Read open thoughts: project-specific rules (e.g. changelog conventions) live on the board itself.

## While you work
- Claim the item: `set E05 status=doing owner=claude`. Sign with `--who <name>` or `BRAINBOARD_WHO`.
- Report progress honestly: `set E05 progress=50`. Progress 100 marks it done.
- Put findings, questions and what you changed (file:line) in notes: `note E05 "…"`.
- If it needs the person's call, set `status=decide` and lay out the options in a note. Don't decide for them.
- New issue? `add "title" --cat "<category>" --priority P1 --issue "…"`.
- Running a side experiment (rerun, ablation, tweak)? `exp-add "title" --problem … --setup … --where … --related E05`,
  then `set X01 status=running`, and at the end `set X01 status=done outcome=worked results="…"`.

Statuses: `todo doing decide blocked done skip` · Priorities: `P1 P2 P3`.
Prefer the CLI to editing the JSON by hand: it locks the file, logs activity and bumps `rev`.
