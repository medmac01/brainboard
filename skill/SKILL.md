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
## Experiments (side runs: reruns, ablations, adjustments)
Each project also tracks experiments (ids `X01`, …). `show`, `set` and `note` work on them like on items.
```bash
bb -p S exp                                  # list: running, planned, then finished
bb -p S exp-add "Rerun baseline with seed 7" --problem "Is the gain seed noise?" \
   --setup "config/command/model" --where "host:~/runs/x" --related E09
bb -p S set X03 status=running               # stamps started; done/failed/dropped stamps ended
bb -p S set X03 status=done outcome=worked results="F1 .41 → .44 (3 seeds)"
```
Experiment statuses: `planned running done failed dropped`. Outcome: `worked inconclusive didnt` (or empty).
Log an experiment when you start a run, and fill in `results` (with the real numbers) when it finishes.
Link it to the items it informs with `related`.

## Paper library (shared by all projects, no `-p` needed)
Papers (`P012`) live in one library; each can be tagged to projects. `show`, `set` and `note` take P ids.
```bash
bb papers                                    # all papers; bb -p S papers = the ones tagged to project S
bb papers -q provenance --reading to-read    # filter titles/authors/tags/venue, reading status
bb show P012                                 # details, abstract, your summary, card, notes
bb paper-text P012 --pages 1-3               # extracted text with "--- page N ---" markers; cite pages
bb grep '"query graph" LLM' -n 20            # full-text search (all terms per page; quotes keep phrases)
bb paper-add arxiv:2401.12345 10.1145/… https://arxiv.org/abs/…   # metadata + PDF fetched
bb paper-add --pdf paper.pdf --projects S --tags provenance       # or --bib refs.bib
bb paper-set P012 reading=read projects=S tags=a,b
bb paper-card P012 --file card.json          # write the structured digest (see below)
bb note P012 "…"                             # discussion on a paper
bb -p S bib > refs.bib                       # BibTeX for a project's papers
```
Reading status (`to-read skimmed read key`) is the user's call; don't change it unless asked.

**Card**: after reading a paper (read the text, not just the abstract), write a card JSON with
`tldr, problem, approach, contributions, evaluation, limitations, gaps, questions` (Markdown strings) and
`datasets, metrics` (lists). It may also carry `short` (the system's name) and `tags`. Be specific: real
numbers in `evaluation`; in `limitations` say what the evaluation does *not* show (leakage, tiny test sets,
re-implemented baselines); in `gaps` and `questions` connect the paper to the user's own work.

## Ideas (research ideas, also in the library)
Ideas (`I03`) have a pitch, open questions, the papers they build on, projects and a status
`raw promising pursuing parked dropped`. `show`, `set` and `note` take I ids.
```bash
bb -p S ideas                                # this project's ideas (bb ideas = all), --status promising
bb -p S idea-add "Short title" --builds-on P17,P18 \
   --pitch "What the idea is, what's new vs the papers, why it matters" \
   --questions "- the first thing to find out\n- the cheapest experiment"
bb set I03 builds_on=P17,P22 tags=hunting    # status is the user's call (they triage raw → promising)
bb -p S idea-promote I03 [--as experiment]   # only when the user asks: creates the item/experiment, links both
```

## Topics (a map of the field, also in the library)
Topics (`C05`) form a tree of research directions (e.g. *CTI-driven hunting › Learned query-graph matching*).
Each has a `position` (what the direction assumes and asks, its lineage, how it differs from its neighbours) and
`questions` (open problems). A paper can be in several topics; its first is its main one. `show`, `set`, `note` take C ids.
```bash
bb topics --papers                           # the tree, paper counts, each topic's papers (* = main), unsorted papers
bb topic-context C05                         # context pack for a deep dive: position, parents, neighbours,
                                             #   every paper's full card and notes, linked ideas (--brief: tl;drs)
bb papers --topic C05 · bb grep --topic C05 '"order embedding"'   # include subtopics
bb find "a research question in plain words" [--topic C05]  # papers ranked by relevance, page-cited passages
                                             #   (the brainboard-library skill covers answering research questions)
bb topic-add "Title" --parent C03 --papers P20,P22 --main --position "…" --questions "- …"
bb topic-papers C05 P31 P23 [--main | --remove]
bb paper-set P31 topics=C05,C01              # or set the whole list (first = main); a card file may carry topics too
bb set C05 position="…" parent=C03 · bb note C05 "…" · bb idea-add … --topics C05
```
- Place a paper by what it *contributes* to the field, not by the techniques it uses. After writing a card, put
  the paper in topics if it has none (`bb topics` lists unsorted papers). Create a new topic only when no existing
  one fits; keep topics as directions a reviewer would recognise.
- Write `position` like a related-work paragraph: the framing, the core assumption, which papers start and
  extend the line, and the boundary with neighbouring topics. Cite papers by id.
- Restructuring the map (merging, splitting, deleting topics) is the user's call; propose it in a note first.

**Deep dive on a topic** (when the user asks to dig into one direction): start with `bb topic-context C05`,
check claims against the text (`grep --topic`, `paper-text --pages`), then report lineage, shared assumptions,
contradictions, evaluation gaps and real open problems in a note on the topic; update `position`/`questions`
only if asked, and add ideas with `--topics C05`.

**Brainstorming with the library** (when the user asks for ideas or directions):
1. Read the cards of the relevant papers first (`bb -p S papers`, or `bb topic-context C..` for one direction,
   then `show P..`), and the existing ideas (`bb -p S ideas`) so you don't repeat them.
2. Check claims against the text with `grep` and `paper-text --pages`; cite as `P012 p.7`.
3. Add each proposal with `idea-add` (status stays `raw`), with `--builds-on` and concrete open questions.
   Prefer a few sharp ideas over many vague ones; say what would falsify each.
4. Leave triage to the user. Don't promote, park or drop ideas unless asked.

## Conventions
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
