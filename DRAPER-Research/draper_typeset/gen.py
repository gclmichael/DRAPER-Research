#!/usr/bin/env python3
"""Split the source markdown into fenced blocks and emit per-teammate inputs."""
import json
import os
import re

SRC = "original.md"
OUT = "."

CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")
MATHOP = re.compile(r"(=|<|>|\^|_|\*|/|\||\+|sum|sqrt|argmin|min|max|log|rho)")


def parse(path):
    text = open(path, encoding="utf-8").read()
    lines = text.split("\n")
    blocks = []
    i = 0
    while i < len(lines):
        m = re.match(r"^```(\S*)", lines[i])
        if m:
            lang = m.group(1)
            open_line = i + 1
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            close_line = j + 1
            body = lines[i + 1 : j]
            blocks.append(
                dict(
                    id="B%03d" % (len(blocks) + 1),
                    lang=lang,
                    open_line=open_line,
                    close_line=close_line,
                    text="\n".join(body),
                )
            )
            i = j + 1
        else:
            i += 1
    return blocks


def classify(b):
    t = b["text"]
    if b["lang"] in ("yaml", "bash"):
        return "CODE_KEEP"
    if re.search(r"^\s*Algorithm", t, re.M):
        return "CODE_KEEP"
    has_cjk = bool(CJK.search(t))
    has_math = bool(MATHOP.search(t))
    if has_cjk and has_math:
        return "LIST?"
    if has_cjk:
        return "QUOTE?"
    return "MATH?"


def main():
    blocks = parse(SRC)
    for b in blocks:
        b["hint"] = classify(b)
    json.dump(blocks, open("blocks.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    groups = {
        "early": ("B001", "B022"),
        "lead": ("B023", "B066"),
        "mid": ("B067", "B102"),
        "late": ("B103", "B113"),
    }
    for name, (lo, hi) in groups.items():
        sel = [b for b in blocks if lo <= b["id"] <= hi]
        json.dump(sel, open("in_%s.json" % name, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    print("blocks: %d  groups: %s" % (len(blocks), groups))
    print()
    for b in blocks:
        flag = b["hint"]
        first = b["text"].split("\n")[0][:70]
        print("%s L%-4d-%4d %-9s %-9s | %s" % (
            b["id"], b["open_line"], b["close_line"], b["lang"], flag, first))
    print()
    from collections import Counter
    print(Counter(b["hint"] for b in blocks))


if __name__ == "__main__":
    main()
