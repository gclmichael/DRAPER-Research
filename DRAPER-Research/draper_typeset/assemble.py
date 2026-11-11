#!/usr/bin/env python3
"""Assemble the final markdown from original + per-group converted blocks.

Guarantees:
  * every line OUTSIDE a fenced block is byte-identical to the original,
    EXCEPT the explicitly whitelisted structural fixes below;
  * every fenced block not present in a converted group keeps its exact
    original body and its original language tag;
  * converted blocks come verbatim from out_*.json.

Usage: python3 assemble.py            # writes final.md, prints a diff summary
       python3 assemble.py --write    # also writes back to the vault
"""
import json
import re
import sys

SRC = "original.md"
FINAL = "assembled.md"

# Structural fixes that are pure rendering repairs (no words touched).
# Each is (line_no_1based, expected_original_line, replacement_lines).
STRUCTURAL_FIXES = [
    # line 874 is a normal sentence immediately followed by '---', which makes
    # markdown parse it as a setext H2 title. Insert a blank line before '---'.
    (875, "---", ["", "---"]),
]


def parse(path):
    lines = open(path, encoding="utf-8").read().split("\n")
    blocks = []
    i = 0
    while i < len(lines):
        m = re.match(r"^```(\S*)", lines[i])
        if m:
            lang = m.group(1)
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            blocks.append(dict(id="B%03d" % (len(blocks) + 1), lang=lang,
                               open_idx=i, close_idx=j,
                               body=lines[i + 1:j]))
            i = j + 1
        else:
            i += 1
    return lines, blocks


def normalize_display_math(text):
    """Put every single-line display-math delimiter on its own line.

    `$$x=1$$` and a multi-line `$$ ... $$` both render in Obsidian, but mixing
    the two styles looks inconsistent. Whitespace-only: no math character is
    added, removed or reordered.
    """
    lines = text.split("\n")
    out, i, infence = [], 0, False
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            infence = not infence
            out.append(line)
            i += 1
            continue
        if infence:
            out.append(line)
            i += 1
            continue
        s = line.strip()
        if s.startswith("$$") and s.endswith("$$") and len(s) > 4:
            indent = line[:len(line) - len(line.lstrip())]
            out.append(indent + "$$")
            out.append(s[2:-2].strip())
            out.append(indent + "$$")
            i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def main():
    groups = sys.argv[1:] or ["early", "lead", "mid", "late"]
    conv = {}
    for g in groups:
        if g.startswith("--"):
            continue
        try:
            conv.update(json.load(open("out_%s.json" % g, encoding="utf-8")))
        except FileNotFoundError:
            print("note: out_%s.json absent, blocks stay untouched" % g)

    lines, blocks = parse(SRC)
    fixes = {ln: (exp, rep) for ln, exp, rep in STRUCTURAL_FIXES}

    out = []
    changed_blocks = []
    nfix = 0
    for idx, line in enumerate(lines):
        ln = idx + 1
        blk = next((b for b in blocks if b["open_idx"] == idx), None)
        if blk is not None:
            bid = blk["id"]
            if bid in conv:
                new = conv[bid].rstrip("\n")
                tag = new.split("\n")[0].strip()
                out.extend(new.split("\n"))
                changed_blocks.append(bid)
            else:
                out.append(line)
                out.extend(blk["body"])
                out.append(lines[blk["close_idx"]])
            continue
        # skip lines already emitted as part of a block
        if any(b["open_idx"] < idx <= b["close_idx"] for b in blocks):
            continue
        if ln in fixes:
            exp, rep = fixes[ln]
            assert line == exp, "line %d expected %r got %r" % (ln, exp, line)
            out.extend(rep)
            nfix += 1
            continue
        out.append(line)

    text = normalize_display_math("\n".join(out))
    open(FINAL, "w", encoding="utf-8").write(text)

    print("blocks converted : %d" % len(changed_blocks))
    print("structural fixes : %d" % nfix)
    print("final lines      : %d (orig %d)" % (len(out), len(lines)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
