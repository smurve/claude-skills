#!/usr/bin/env python3
"""
Obsidian vault BFS traversal.

Given a vault root and a starting note, walks the link graph (wiki-links,
embeds, markdown links, backlinks) up to a configurable depth and emits a
JSON graph to stdout. Used by the obsidian-digest skill — the skill does the
summarization; this script only handles the mechanical graph extraction.

Stdlib only. No third-party dependencies.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import urllib.parse
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Regexes
# --------------------------------------------------------------------------- #

# [[Target]] or [[Target|Alias]] or [[Target#Heading]] or [[Target#^block]]
WIKILINK_RE = re.compile(
    r"(?P<embed>!)?\[\[(?P<target>[^\]\n|#^]+)"
    r"(?:#\^?(?P<frag>[^\]|\n]+))?"
    r"(?:\|(?P<alias>[^\]\n]+))?\]\]"
)

# [display](path.md) — only keep .md targets. Allow spaces (Obsidian
# sometimes emits literal spaces; sometimes URL-encodes as %20). We
# URL-decode at extraction time.
MDLINK_RE = re.compile(r"\[(?P<display>[^\]]+)\]\((?P<target>[^)#]+?\.md)(?:#[^)]*)?\)")

# #tag or #nested/tag (not in URLs, not at end of # comment)
TAG_RE = re.compile(r"(?:^|(?<=\s))#([A-Za-z0-9_][A-Za-z0-9_/\-]*)")

# YAML frontmatter delimiter
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)

# Fenced code block — we strip these before link/tag extraction
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass
class Note:
    path: Path              # absolute path
    rel: str                # path relative to vault root (POSIX)
    title: str              # basename without .md
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    frontmatter: dict = field(default_factory=dict)
    body: str = ""
    outlinks: list[dict] = field(default_factory=list)   # [{target, kind, frag}]
    word_count: int = 0


# --------------------------------------------------------------------------- #
# Vault index
# --------------------------------------------------------------------------- #


class Vault:
    """Indexes the vault so we can resolve links by basename or alias."""

    def __init__(self, root: Path, exclude_globs: list[str]):
        self.root = root.resolve()
        self.exclude_globs = exclude_globs
        self.by_rel: dict[str, Path] = {}
        self.by_basename: dict[str, list[Path]] = {}
        self.by_alias: dict[str, Path] = {}
        self._build()

    def _excluded(self, rel: str) -> bool:
        return any(fnmatch.fnmatch(rel, pat) or rel.startswith(pat) for pat in self.exclude_globs)

    def _build(self) -> None:
        for p in self.root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".md", ".canvas"}:
                continue
            # Skip anything under .obsidian/, .trash/, .git/
            rel_parts = p.relative_to(self.root).parts
            if any(part.startswith(".") for part in rel_parts):
                continue
            rel = p.relative_to(self.root).as_posix()
            if self._excluded(rel):
                continue
            self.by_rel[rel] = p
            base = p.stem
            self.by_basename.setdefault(base, []).append(p)

        # Second pass: read frontmatter aliases so we can resolve [[Alias]]
        for rel, path in self.by_rel.items():
            if path.suffix != ".md":
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm = parse_frontmatter(text)
            aliases = fm.get("aliases") or fm.get("alias") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            for alias in aliases:
                if isinstance(alias, str):
                    self.by_alias[alias.strip()] = path

    def resolve(self, target: str) -> Path | None:
        """Resolve an Obsidian link target (a string) to a vault path."""
        target = target.strip()
        if not target:
            return None
        # 1. Exact relative path (with or without .md)
        candidates = [target, f"{target}.md"]
        for c in candidates:
            if c in self.by_rel:
                return self.by_rel[c]
        # 2. Basename match
        basename = Path(target).stem
        if basename in self.by_basename:
            matches = self.by_basename[basename]
            if len(matches) == 1:
                return matches[0]
            # Ambiguous — prefer shortest rel path (top-level wins)
            return min(matches, key=lambda p: len(p.relative_to(self.root).parts))
        # 3. Alias match
        if target in self.by_alias:
            return self.by_alias[target]
        return None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def parse_frontmatter(text: str) -> dict:
    """Very small YAML-ish parser for the subset Obsidian uses in practice."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    body = m.group(1)
    out: dict = {}
    current_key: str | None = None
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- ") and current_key is not None:
            val = line[2:].strip().strip('"').strip("'")
            existing = out.get(current_key)
            if isinstance(existing, list):
                existing.append(val)
            else:
                out[current_key] = [val]
            continue
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            current_key = key
            if not value:
                out[key] = []
            elif value.startswith("[") and value.endswith("]"):
                inner = value[1:-1]
                parts = [p.strip().strip('"').strip("'") for p in inner.split(",") if p.strip()]
                out[key] = parts
            else:
                out[key] = value.strip('"').strip("'")
    return out


def strip_code(text: str) -> str:
    """Remove fenced and inline code so we don't pick up links inside examples."""
    text = FENCE_RE.sub("", text)
    text = INLINE_CODE_RE.sub("", text)
    return text


def extract_links(text: str) -> list[dict]:
    """Return all outlinks from a note body. Kinds: wikilink, embed, mdlink."""
    stripped = strip_code(text)
    links: list[dict] = []
    for m in WIKILINK_RE.finditer(stripped):
        target = m.group("target").strip()
        kind = "embed" if m.group("embed") else "wikilink"
        links.append({"target": target, "kind": kind, "frag": m.group("frag")})
    for m in MDLINK_RE.finditer(stripped):
        target = urllib.parse.unquote(m.group("target").strip())
        links.append({"target": target, "kind": "mdlink", "frag": None})
    return links


def extract_tags(text: str, frontmatter: dict) -> list[str]:
    tags: set[str] = set()
    fm_tags = frontmatter.get("tags") or frontmatter.get("tag") or []
    if isinstance(fm_tags, str):
        fm_tags = [fm_tags]
    for t in fm_tags:
        if isinstance(t, str):
            tags.add(f"#{t.lstrip('#')}")
    for m in TAG_RE.finditer(strip_code(text)):
        tags.add(f"#{m.group(1)}")
    return sorted(tags)


def extract_canvas_links(text: str) -> list[dict]:
    """Obsidian .canvas files are JSON with nodes that may reference files."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    links: list[dict] = []
    for node in data.get("nodes", []):
        f = node.get("file")
        if f:
            links.append({"target": f, "kind": "canvas", "frag": None})
    return links


def load_note(vault: Vault, path: Path) -> Note:
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = path.relative_to(vault.root).as_posix()
    title = path.stem

    if path.suffix == ".canvas":
        outlinks = extract_canvas_links(text)
        return Note(
            path=path, rel=rel, title=title, body="", outlinks=outlinks,
            word_count=0,
        )

    fm = parse_frontmatter(text)
    body_text = FRONTMATTER_RE.sub("", text, count=1)
    outlinks = extract_links(body_text)
    tags = extract_tags(body_text, fm)
    aliases = fm.get("aliases") or fm.get("alias") or []
    if isinstance(aliases, str):
        aliases = [aliases]

    return Note(
        path=path,
        rel=rel,
        title=title,
        aliases=[a for a in aliases if isinstance(a, str)],
        tags=tags,
        frontmatter=fm,
        body=body_text,
        outlinks=outlinks,
        word_count=len(body_text.split()),
    )


# --------------------------------------------------------------------------- #
# Backlink discovery
# --------------------------------------------------------------------------- #


def find_backlinks(vault: Vault, target: Note) -> list[Path]:
    """
    Scan the vault for notes that wiki-link or md-link to `target`.
    O(N) in vault size — fine for vaults <10k notes; swap for ripgrep if huge.
    """
    needle_basename = target.path.stem
    needle_rel = target.rel
    needle_rel_noext = needle_rel[:-3] if needle_rel.endswith(".md") else needle_rel
    aliases = set(target.aliases)

    backlinks: list[Path] = []
    for rel, path in vault.by_rel.items():
        if path == target.path or path.suffix != ".md":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        stripped = strip_code(text)
        for m in WIKILINK_RE.finditer(stripped):
            tgt = m.group("target").strip()
            if tgt == needle_basename or tgt == needle_rel_noext or tgt in aliases:
                backlinks.append(path)
                break
        else:
            for m in MDLINK_RE.finditer(stripped):
                if urllib.parse.unquote(m.group("target").strip()) == needle_rel:
                    backlinks.append(path)
                    break
    return backlinks


# --------------------------------------------------------------------------- #
# BFS
# --------------------------------------------------------------------------- #


def bfs(
    vault: Vault,
    start: Path,
    depth: int,
    direction: str,
    include_embeds: bool,
    max_notes: int,
) -> tuple[dict[str, Note], list[dict], list[dict], bool]:
    """
    Returns (notes_by_rel, edges, orphans, truncated).
    """
    notes: dict[str, Note] = {}
    edges: list[dict] = []
    orphans: list[dict] = []
    distances: dict[str, int] = {}
    queue: deque[tuple[Path, int]] = deque([(start, 0)])
    truncated = False

    while queue:
        path, dist = queue.popleft()
        rel = path.relative_to(vault.root).as_posix()
        if rel in notes:
            continue
        if len(notes) >= max_notes:
            truncated = True
            break

        note = load_note(vault, path)
        notes[rel] = note
        distances[rel] = dist

        if dist >= depth:
            continue

        # Forward edges
        if direction in ("forward", "both"):
            for link in note.outlinks:
                if link["kind"] == "embed" and not include_embeds:
                    continue
                resolved = vault.resolve(link["target"])
                if resolved is None:
                    orphans.append(
                        {"from": rel, "target": link["target"], "kind": link["kind"]}
                    )
                    continue
                target_rel = resolved.relative_to(vault.root).as_posix()
                edges.append({"from": rel, "to": target_rel, "kind": link["kind"]})
                if target_rel not in notes:
                    queue.append((resolved, dist + 1))

        # Backward edges
        if direction in ("backward", "both"):
            for src_path in find_backlinks(vault, note):
                src_rel = src_path.relative_to(vault.root).as_posix()
                edges.append({"from": src_rel, "to": rel, "kind": "backlink"})
                if src_rel not in notes:
                    queue.append((src_path, dist + 1))

    return notes, edges, orphans, truncated


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def resolve_starting_file(vault: Vault, file_arg: str) -> Path:
    p = Path(file_arg)
    if p.is_absolute() and p.exists():
        return p.resolve()
    # Try relative to vault
    candidate = (vault.root / file_arg).resolve()
    if candidate.exists():
        return candidate
    # Basename/alias fallback via index
    resolved = vault.resolve(file_arg)
    if resolved is not None:
        return resolved
    # One last try with .md appended
    resolved = vault.resolve(f"{file_arg}.md")
    if resolved is not None:
        return resolved
    raise SystemExit(f"error: could not resolve starting file: {file_arg}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Obsidian vault BFS traversal")
    ap.add_argument("--vault", required=True, help="Vault root (directory containing .obsidian/)")
    ap.add_argument("--file", required=True, help="Starting note (path, basename, or alias)")
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--direction", choices=["forward", "backward", "both"], default="both")
    ap.add_argument("--include-embeds", action="store_true", default=True)
    ap.add_argument("--no-embeds", dest="include_embeds", action="store_false")
    ap.add_argument("--max-notes", type=int, default=50)
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--no-body", action="store_true", help="Omit note bodies from JSON output")
    args = ap.parse_args()

    vault_root = Path(args.vault).resolve()
    if not (vault_root / ".obsidian").exists():
        print(
            f"warning: {vault_root} has no .obsidian/ folder — is this really a vault?",
            file=sys.stderr,
        )

    vault = Vault(vault_root, exclude_globs=args.exclude)
    start = resolve_starting_file(vault, args.file)

    notes, edges, orphans, truncated = bfs(
        vault=vault,
        start=start,
        depth=args.depth,
        direction=args.direction,
        include_embeds=args.include_embeds,
        max_notes=args.max_notes,
    )

    nodes_out: list[dict] = []
    start_rel = start.relative_to(vault.root).as_posix()
    for rel, note in notes.items():
        node = {
            "path": rel,
            "title": note.title,
            "aliases": note.aliases,
            "tags": note.tags,
            "distance": _distance(rel, start_rel, edges),
            "word_count": note.word_count,
            "frontmatter": note.frontmatter,
        }
        if not args.no_body:
            node["body"] = note.body
        nodes_out.append(node)

    output = {
        "vault_root": str(vault_root),
        "root": start_rel,
        "depth": args.depth,
        "direction": args.direction,
        "truncated": truncated,
        "max_notes": args.max_notes,
        "nodes": nodes_out,
        "edges": edges,
        "orphans": orphans,
    }
    json.dump(output, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def _distance(rel: str, start_rel: str, edges: list[dict]) -> int:
    """Shortest-path distance from start to rel in the (undirected) edge set."""
    if rel == start_rel:
        return 0
    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e["from"], set()).add(e["to"])
        adj.setdefault(e["to"], set()).add(e["from"])
    seen = {start_rel}
    queue: deque[tuple[str, int]] = deque([(start_rel, 0)])
    while queue:
        node, d = queue.popleft()
        if node == rel:
            return d
        for n in adj.get(node, ()):
            if n not in seen:
                seen.add(n)
                queue.append((n, d + 1))
    return -1


if __name__ == "__main__":
    main()
