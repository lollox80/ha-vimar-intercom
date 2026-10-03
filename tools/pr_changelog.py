#!/usr/bin/env python3
"""Read the `Changelog:` and `Before you update:` lines of a PR description.

The changelog is written at release time from these lines (no PR edits CHANGELOG.md).
A PR has one or more lines, possibly in different sections and in a bulleted list:

    Changelog: Bug fixes - The door no longer opens twice over the cloud (#120)
    Changelog: Documentation - New page on the Echo Show setup
    Before you update: Home Assistant 2025.10 or newer is now required

or the single line `Changelog: none`. HTML comments (the template's examples) and fenced code
blocks (examples in the text) are ignored.

Usage in CI:  PR_BODY="..." python tools/pr_changelog.py   (exit 0 ok, 1 with the problems)
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

SECTIONS = ("Enhancements", "Bug fixes", "Documentation", "Security", "Other changes")

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE = re.compile(r"^\s*(```|~~~)")
# Optional bullet ("- ", "* ", "1. ") and bold markers around the key.
_PREFIX = r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?\**"
_CHANGELOG = re.compile(_PREFIX + r"changelog\**\s*:\**\s*(?P<rest>.*)$", re.IGNORECASE)
_BEFORE = re.compile(_PREFIX + r"before you update\**\s*:\**\s*(?P<rest>.*)$", re.IGNORECASE)
# "<section> - <text>", with a hyphen, an en dash or an em dash, or a colon.
_ENTRY = re.compile(r"^(?P<section>[^-–—:]+?)\s*(?:[-–—]|:)\s*(?P<text>.+)$")


@dataclass
class Result:
    entries: list[tuple[str, str]] = field(default_factory=list)
    before_you_update: list[str] = field(default_factory=list)
    none: bool = False
    problems: list[str] = field(default_factory=list)


def parse(body: str | None) -> Result:
    res = Result()
    text = _COMMENT.sub("", body or "")
    lines = 0
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        if m := _BEFORE.match(line):
            note = m["rest"].strip()
            if note:
                res.before_you_update.append(note)
            else:
                res.problems.append("an empty 'Before you update:' line")
            continue
        m = _CHANGELOG.match(line)
        if not m:
            continue
        lines += 1
        rest = m["rest"].strip()
        if rest.lower() == "none":
            res.none = True
            continue
        e = _ENTRY.match(rest)
        if not e:
            res.problems.append(f"'Changelog: {rest}' needs '<section> - <what the user sees>'")
            continue
        section = next((s for s in SECTIONS if s.lower() == e["section"].strip().lower()), None)
        if not section:
            res.problems.append(f"unknown section in 'Changelog: {rest}' (use one of: {', '.join(SECTIONS)})")
            continue
        res.entries.append((section, e["text"].strip()))
    if lines == 0:
        res.problems.append(
            "no 'Changelog:' line: add 'Changelog: <section> - <what the user sees>' or 'Changelog: none'")
    elif res.none and lines > 1:
        res.problems.append("'Changelog: none' must be the only 'Changelog:' line")
    return res


def main() -> int:
    res = parse(os.environ.get("PR_BODY", ""))
    for section, text in res.entries:
        print(f"{section}: {text}")
    for note in res.before_you_update:
        print(f"Before you update: {note}")
    if res.none and not res.problems:
        print("Changelog: none")
    for problem in res.problems:
        print(f"::error::{problem}")
    return 1 if res.problems else 0


if __name__ == "__main__":
    sys.exit(main())
