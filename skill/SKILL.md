---
name: brainboard
description: Shared project board between the user and their AI agents (items with priority/status/progress, decisions waiting on the user, a brain-dump of thoughts, a notes thread on every item). Use whenever the user says "check the board", "check the hub", "put it on the board", "what's next", mentions brainboard, or asks you to report progress, raise a question, or log a decision for one of their projects.
---

# brainboard

The board is the user's shared to-do and decision list across **projects**. The user works in a browser;
you use the CLI below. Everything you write is visible to them within seconds, signed as `{{WHO}}`.

The CLI is `{{BB}}` (written as `bb` below; use the full path, since shell variables don't persist
between your commands). It signs as {{WHO}} unless `BRAINBOARD_WHO` is set.

## Pick the project
```bash
bb projects                      # * marks the project commands will use
bb -p <slug> summary             # -p takes a slug, a title, or a unique slug prefix
```
Resolution order: `-p`, `$BRAINBOARD_PROJECT`, a `.brainboard` file (one line: the slug) in the cwd or a
parent, the config default (`bb use <slug>`), or the only active project. If several projects match the
request and nothing pins one, ask the user which project — don't guess.

## "Check the board"
1. `bb -p <slug> inbox`. This lists, **per item**, every note by the user (`me`) posted after your own last
   note on that item, plus the user's open thoughts. `--from all` also includes other agents' notes. Don't filter by a global timestamp. Notes
   written between your reads are easy to miss that way.
2. Answer **every** entry: do the work, ask a question, or say it's parked. Reply with `note`, then set the
   status (`done`, `blocked`, or `decide`).
3. `bb -p <slug> next` for what to work on after that (decisions first, then doing, then P1 todos).

## While you work
```bash
bb -p S show E05                          # full item incl. notes thread
bb -p S set E05 status=doing owner={{WHO}}   # claim it
bb -p S set E05 progress=50               # honest progress; 100 marks it done
bb -p S note E05 "Changed X in path/file.py:42 because …"
bb -p S set E05 status=decide             # needs the user's call; put the options in a note first
bb -p S add "New issue" --cat Inbox --priority P1 --issue "…"
bb -p S thought "Question for the user: …"
bb -p S list --status todo,doing --priority P1
```
- Statuses: `todo doing decide blocked done skip`. Priorities: `P1 P2 P3`.
- When something needs the user's choice, set `status=decide` and lay out the options. Don't decide for them.
- Put findings, what you changed (with file:line), and open questions in notes, not only in chat.
- Read the project's open thoughts. Project-specific rules often live there.
- Use the CLI, never edit the JSON by hand. The CLI locks the file, logs activity and bumps the revision.

## Setup (only if a command says it can't reach the board)
```bash
bb config --url http://HOST:PORT   # prompts for the password; stored in ~/.config/brainboard/config.json (600)
bb projects
```
Never print, commit or paste the board password.
