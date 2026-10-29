---
name: brainboard-library
description: The user's research paper library (threat hunting, provenance, APT detection, CTI, LLM agents for security, …) with agent-written cards, a topic map of the field and full text. Use whenever the user asks a research question or wants literature support - "what do the papers say about…", "which papers…", "who does X", "is there prior work on…", "support this with references", "compare A and B", "related work for…", "find papers on…", "what's the gap in…", while discussing an idea, a reviewer comment or a section of their paper - even if they don't mention the library.
---

# Answering with the user's paper library

The user keeps the papers they read in brainboard's library: each paper (`P12`) has metadata, a *card* written
after a careful reading (tldr, problem, approach, contributions, evaluation, limitations, gaps, questions,
datasets, metrics), the user's own summary, notes, and the PDF's text by page. Papers sit in **topics** (`C05`),
a tree of research directions, each with a written position in the field and open problems.

The CLI is `{{BB}}` (written `bb` below; always use the full path).

## 1. Find the relevant papers
```bash
bb find "the user's question in plain words"            # ranked papers, page-cited passages, the topics they cluster in
bb find "…" --topic C06                                 # stay inside one direction (and its subtopics)
bb find "…" -n 12 --full                                # more papers, each with its whole card
bb topics                                               # the map, if you need to pick a scope
```
`find` is lexical (BM25 over cards and page text), so rephrase if results look off: use the field's own words
(e.g. "query graph", "provenance", "order embedding"), add synonyms, or split a compound question into two finds.
For an exact phrase use `bb grep '"phrase"' --topic C06`.

## 2. Read before you answer
- `bb show P12` for the full card and the user's notes; `bb paper-text P12 --pages 7-8` to read around a passage.
- If the question is about a whole direction ("what's the state of X?"), load it: `bb topic-context C06`.
- Check every claim you will make against the card or the text. Cards are the user's critical reading; quote
  numbers from `evaluation` and caveats from `limitations` rather than from abstracts.

## 3. Answer
- Cite as **P12 (ShortName) p.7**, so the user can open it in brainboard. Name the topic when it helps them place a
  paper ("P22 ProvG-Searcher, in C06 Learned query-graph matching").
- Separate what the papers say from your own inference, and say when the library has nothing on a point ("none of
  the library's papers evaluates X") rather than filling it from memory. If you add papers from your own knowledge, mark
  them as not in the library and offer to add them (`bb paper-add arxiv:… / DOI`).
- Prefer the user's framing: relate findings to their own project (read the topic's position and the papers' `gaps`).
- Be critical: point out leakage, tiny test sets, re-implemented baselines, and disagreements between papers.

## 4. Keep what's worth keeping (only when it's useful, and say you did it)
```bash
bb note C06 "…"            # a finding about a direction (what the papers agree/disagree on, with citations)
bb note P12 "…"            # something you noticed in one paper
bb idea-add "Title" --topics C06 --builds-on P20,P22 --pitch "…" --questions "- …"   # a new research idea (stays raw)
```
Don't change reading status, topic structure or idea status unless asked; those are the user's calls.
