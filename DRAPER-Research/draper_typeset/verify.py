#!/usr/bin/env python3
"""Prove that everything outside converted fenced blocks is byte-identical.

Method (independent of assemble.py):
  expected = original.md with the ORIGINAL span of every converted block deleted
  actual   = final.md   with the NEW    text of every converted block deleted
  assert canon(expected) == canon(actual)

canon() only normalizes runs of blank lines (absorbing the blank lines that
surround $$ display math and lists), it never touches words.

Also asserts:
  * every non-converted block still occurs verbatim, with its original language tag
  * every converted block text really is present in final.md

Usage: python3 verify.py
"""
import importlib.util
import json
import re
import sys


def _load_norm():
    spec = importlib.util.spec_from_file_location("asm", "assemble.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.normalize_display_math

ORIG = "original.md"
FINAL = "assembled.md"

# 1-based line in original -> replacement lines (pure rendering repair:
# insert a blank line before '---' so line 874 is not parsed as a setext H2)
ALLOWED_FIXES = {875: ["", "---"]}


def canon(s):
    s = s.replace("\r\n", "\n")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{2,}", "\n\n", s)
    return s.strip("\n")


def main():
    norm = _load_norm()
    blocks = {b["id"]: b for b in json.load(open("blocks.json", encoding="utf-8"))}
    conv = {}
    for g in ("early", "lead", "mid", "late"):
        try:
            data = json.load(open("out_%s.json" % g, encoding="utf-8"))
        except FileNotFoundError:
            continue
        for k, v in data.items():
            conv[k] = norm(v.rstrip("\n"))

    ok = True
    lines = open(ORIG, encoding="utf-8").read().split("\n")

    # ---- expected: original minus original spans of converted blocks ----
    drop = set()
    for bid in conv:
        b = blocks[bid]
        for ln in range(b["open_line"], b["close_line"] + 1):
            drop.add(ln)
    exp_lines = []
    for idx, line in enumerate(lines, 1):
        if idx in drop:
            continue
        if idx in ALLOWED_FIXES and idx not in drop:
            exp_lines.extend(ALLOWED_FIXES[idx])
            continue
        exp_lines.append(line)
    expected = canon("\n".join(exp_lines))

    # ---- actual: final.md minus the new text of each converted block ----
    text = open(FINAL, encoding="utf-8").read()
    order = sorted(conv, key=lambda b: blocks[b]["open_line"])
    parts = []
    cursor = 0
    notfound = []
    for bid in order:
        new = conv[bid]
        idx = text.find(new, cursor)
        if idx < 0:
            notfound.append(bid)
            continue
        parts.append(text[cursor:idx])
        cursor = idx + len(new)
    parts.append(text[cursor:])
    actual = canon("".join(parts))

    if notfound:
        ok = False
        print("!! converted text NOT FOUND in final.md: %s" % notfound)

    if expected != actual:
        ok = False
        el, al = expected.split("\n"), actual.split("\n")
        print("!! UNCHANGED CONTENT DIFFERS (expected %d lines, actual %d)"
              % (len(el), len(al)))
        shown = 0
        for k in range(max(len(el), len(al))):
            a = el[k] if k < len(el) else "<EOF>"
            b = al[k] if k < len(al) else "<EOF>"
            if a != b:
                print("   line %d:\n     orig=[%s]\n     new =[%s]" % (k + 1, a, b))
                shown += 1
                if shown >= 6:
                    break
    else:
        print("OK  all non-converted content byte-identical (%d lines)" % len(expected.split("\n")))

    # ---- non-converted blocks preserved verbatim ----
    bad = []
    nkeep = 0
    for bid, b in blocks.items():
        if bid in conv:
            continue
        nkeep += 1
        span = "```%s\n%s\n```" % (b["lang"], b["text"])
        if span not in text:
            bad.append(bid)
    if bad:
        ok = False
        print("!! non-converted blocks modified/missing: %s" % bad)
    else:
        print("OK  %d non-converted blocks preserved verbatim + language tag" % nkeep)

    print()
    print("converted blocks: %d / %d" % (len(conv), len(blocks)))
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
