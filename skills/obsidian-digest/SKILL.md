---
name: obsidian-digest
description: >
  Use when the user wants to condense, summarize, digest, or build a knowledge
  base from an Obsidian vault starting at a specific note. Traverses wiki-links,
  embeds, backlinks, and markdown links up to a configurable depth (default 1),
  resolves aliases/headings/block refs, and produces a structured condensed
  knowledge base. Trigger on phrases like "digest this note", "summarize my
  Obsidian notes on X", "build a knowledge base from [[note]]", "condense
  everything linked to this file", or any request that operates on `.md` files
  inside a folder containing `.obsidian/`.
---

# Obsidian Digest

Condense an Obsidian vault subgraph — starting from one note and expanding
outward through links — into a single structured knowledge base document.

## What This Skill Produces

Given a starting note and a depth, this skill outputs a single markdown
document with:

1. **Executive summary** — the core thesis of the starting note + the main
   themes discovered in the traversed subgraph.
2. **Concept map** — a bulleted hierarchy of notes, grouped by shared tags and
   linkage proximity to the root.
3. **Per-note digests** — a compressed (3–8 sentence) summary of each traversed
   note, preserving its *unique* contribution (deduplicated against siblings).
4. **Cross-references** — which notes cite which, rendered as a compact table.
5. **Tag index** — all `#tags` found, grouped into clusters.
6. **Orphans & dead ends** — links that point to missing notes, so the user can
   fix them.

The output is designed to be pasted back into the vault as a "Map of Content"
(MOC) note.

## Parameters

| Parameter         | Default   | Description                                              |
| ----------------- | --------- | -------------------------------------------------------- |
| `file` (required) | —         | Starting note (path relative to vault, or just the name) |
| `depth`           | `1`       | Max hops from the starting note through links            |
| `direction`       | `both`    | `forward` (outlinks), `backward` (backlinks), or `both`  |
| `include_embeds`  | `true`    | Follow `![[embed]]` transclusions as edges               |
| `follow_tags`     | `false`   | Also pull in notes sharing ≥2 tags with the root         |
| `exclude`         | `[]`      | Folder globs to skip (e.g. `Templates/`, `Archive/`)     |
| `max_notes`       | `50`      | Hard cap to prevent runaway traversal                    |
| `format`          | `markdown`| `markdown` or `json`                                     |
| `output`          | stdout    | Output path (often `Digests/YYYY-MM-DD - <root>.md`)     |

## When to Use

- User says "digest", "condense", "summarize subgraph", "build MOC", or
  "knowledge base from [[note]]".
- User is clearly working inside an Obsidian vault (presence of `.obsidian/`).
- User has a large linked cluster of notes they want compressed into one view.

## When NOT to Use

- Single-file summarization with no linking → just summarize the file directly.
- Full-vault export → use Obsidian's built-in export or a bespoke script;
  `max_notes` is a safety cap, not a full-vault strategy.
- Non-Obsidian markdown folders with no `[[wiki-links]]` → use a plain
  markdown summarizer instead.

## Workflow

### Step 1 — Locate the vault root

Find the nearest ancestor directory of the starting file that contains a
`.obsidian/` folder. That is the vault root; **all link resolution must be
relative to it**, never to the starting file's directory. Obsidian uses
*vault-global* note names, not relative paths.

If no `.obsidian/` is found, ask the user to confirm the vault root before
proceeding.

### Step 2 — Resolve the starting note

Obsidian allows linking by basename, by relative path, or by alias. Resolve
the user-provided `file` argument in this order:

1. Exact path match from vault root.
2. Basename match (walk the vault, find `<file>.md`).
3. Alias match — scan frontmatter `aliases:` lists for a match.

If multiple candidates exist, list them and ask the user to disambiguate.

### Step 3 — Traverse the link graph

Use the helper script `scripts/traverse.py` (see below). It performs a BFS
from the starting note up to `depth` hops and returns a JSON graph:

```json
{
  "vault_root": "/abs/path/to/vault",
  "root": "Notes/Topic.md",
  "depth": 1,
  "nodes": [
    {
      "path": "Notes/Topic.md",
      "title": "Topic",
      "aliases": ["Alt"],
      "tags": ["#research", "#ml"],
      "distance": 0,
      "word_count": 812,
      "frontmatter": {...},
      "body": "..."
    }
  ],
  "edges": [
    {"from": "Notes/Topic.md", "to": "Notes/Related.md", "kind": "wikilink"},
    {"from": "Notes/Other.md",  "to": "Notes/Topic.md",  "kind": "backlink"},
    {"from": "Notes/Topic.md", "to": "Notes/Img.md",    "kind": "embed"}
  ],
  "orphans": [
    {"from": "Notes/Topic.md", "target": "MissingNote", "line": 42}
  ]
}
```

Run it like:

```bash
python3 scripts/traverse.py \
  --vault "<VAULT_ROOT>" \
  --file "<STARTING_FILE>" \
  --depth 1 \
  --direction both \
  --include-embeds \
  --max-notes 50 \
  --exclude "Templates/" --exclude "Archive/"
```

**What the script handles correctly:**

- `[[Note]]`, `[[Note|Display]]`, `[[Note#Heading]]`, `[[Note#^block-id]]`
- `![[Embed]]`, `![[image.png]]` (embeds become edges with `kind: "embed"`)
- `[markdown](path/to/note.md)` style links
- YAML frontmatter parsing for `aliases:` and `tags:`
- Inline `#tag` and `#nested/tag` extraction (skipping code fences)
- Backlinks discovered via a ripgrep-style scan across the vault
- `.canvas` files (JSON) — node `file` references are treated as outlinks
- Skipping fenced code blocks when extracting links/tags (so `[[example]]`
  inside a ```` ``` ```` block is not followed)
- Alias resolution — a link `[[Alt]]` resolves to the note whose frontmatter
  declares `aliases: [Alt]`
- `exclude` glob filtering (applied to every discovered path)
- `max_notes` hard cap (BFS stops early, records truncation in output)

### Step 4 — Read and compress each node

For each node in the returned graph, in BFS order:

1. If the node's body is already present in the JSON (script returns it for
   efficiency), use that. Otherwise `Read` the file.
2. Produce a **3–8 sentence digest** capturing:
   - The note's thesis or main claim.
   - Its *unique* contribution relative to siblings (dedupe aggressively — if
     two notes make the same point, say so once and cite both).
   - Any concrete facts, numbers, decisions, or commitments.
3. Extract "pull-quote" lines the user explicitly marked as important —
   Obsidian conventions: `==highlighted==`, `> [!note]` callouts, or
   bold+italic `***text***`.

**Do not copy long prose verbatim.** The point of a digest is compression.
Aim for ~10–15% of original word count per note.

### Step 5 — Assemble the knowledge base

Produce a single markdown document with this exact structure:

```markdown
# Digest: <Root Title>

> Generated <YYYY-MM-DD> · depth=<N> · <K> notes traversed · direction=<both|fwd|bwd>

## Executive Summary

<3–6 sentences synthesizing the whole subgraph, not just the root.>

## Concept Map

- **<Cluster 1 name, from dominant shared tag>**
  - [[Note A]] — one-line hook
  - [[Note B]] — one-line hook
- **<Cluster 2 name>**
  - [[Note C]] — one-line hook

## Notes

### [[Root Note]] *(distance 0)*
<3–8 sentence digest>

**Key quotes:**
> ...

### [[Note A]] *(distance 1, ← Root)*
<digest>

...

## Cross-References

| From          | → | To            | Kind     |
| ------------- | - | ------------- | -------- |
| [[Root Note]] | → | [[Note A]]    | wikilink |
| [[Note B]]    | → | [[Root Note]] | backlink |

## Tag Index

- `#ml` — [[Note A]], [[Note C]]
- `#research` — [[Root Note]], [[Note B]]

## Orphans & Dead Ends

- [[MissingNote]] — referenced by [[Root Note]] (line 42) but does not exist
```

Preserve `[[wikilink]]` syntax in the output so the digest itself is a valid
Obsidian note the user can drop into their vault.

### Step 6 — Write the output

- If `output` was provided → `Write` to that path.
- Otherwise → print the full document to stdout.
- If writing into the vault, default path is `Digests/<YYYY-MM-DD> - <root>.md`
  and add YAML frontmatter:

  ```yaml
  ---
  generated: 2026-04-09
  source: "[[Root Note]]"
  depth: 1
  notes_count: 12
  tags: [digest, auto-generated]
  ---
  ```

## Advanced Features

These are automatic unless the user opts out:

- **Alias resolution** — `[[Alt]]` resolves via frontmatter `aliases`.
- **Heading & block refs** — `[[Note#Heading]]` and `[[Note#^block]]` are
  normalized to the target note for graph purposes, but the specific
  heading/block is preserved in the digest as context for *why* this note
  was cited.
- **Canvas files** — `.canvas` JSON nodes with `file` keys participate in the
  graph as regular nodes.
- **Code-fence safety** — links inside ``` fences are ignored (prevents
  pulling in tutorial example notes).
- **Dedup across siblings** — when two notes at the same distance make the
  same claim, the digest merges them and lists both as sources.
- **Tag-expansion mode** (`--follow-tags`) — pulls in notes sharing ≥2 tags
  with the root, even if not linked. Great for discovering thematic siblings
  the user forgot to cross-link.
- **Bidirectional distance** — a note at depth 1 via backlink *and* forward
  link is ranked as more central than one reached only one way.
- **Orphan report** — broken `[[links]]` are collected so the user can fix
  their vault.
- **Token safety** — if `max_notes` is hit, the digest clearly flags
  truncation and lists the notes that were skipped, ordered by distance.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Resolving links relative to the file's directory | Obsidian uses vault-global names. Always resolve from vault root. |
| Treating `[[A\|B]]` as a link to `B` | The target is `A`; `B` is just display text. |
| Following `[[links]]` inside code fences | Strip fenced code before extracting links. |
| Copying note bodies verbatim into the digest | The point is compression — aim for 10–15% of original length. |
| Forgetting backlinks | Backlinks are at least as important as forward links for understanding a note's role in the vault. |
| Following `depth > 2` on large vaults without a cap | Always respect `max_notes`; subgraphs explode quickly. |
| Ignoring `.obsidian/app.json` excluded folders | Check for `userIgnoreFilters` and merge with user `exclude`. |

## Red Flags — Stop and Ask

- Starting file not found → ask the user to clarify (don't guess).
- Multiple notes match by basename → list candidates, ask which one.
- No `.obsidian/` anywhere up the tree → confirm this is really a vault.
- Vault has >5000 notes and user requested `depth ≥ 3` without `max_notes` →
  warn about cost before traversing.

## Quick Reference

```bash
# Default: summarize the note and its immediate neighborhood
obsidian-digest "My Note.md"

# Go two hops, only forward links
obsidian-digest "My Note.md" --depth 2 --direction forward

# Include thematic siblings via shared tags
obsidian-digest "My Note.md" --follow-tags

# Write the result back into the vault
obsidian-digest "My Note.md" --output "Digests/2026-04-09 - My Note.md"
```

## Implementation Notes

The traversal script is at `scripts/traverse.py`. It uses only the Python
standard library (no `obsidiantools` or third-party deps) so it runs in any
environment. If the user has a very large vault, the script can be swapped
for a ripgrep-based frontend — the JSON contract is what matters.
