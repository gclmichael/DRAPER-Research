#!/usr/bin/env python3
"""Prove the inline pass changed ONLY markup/transliteration, never content.

Uses the same canonicalizer as check.py: both sides are normalized to a common
ASCII form (``X`` and $X$ and \\text{X} all collapse), then compared exactly on
CJK chars, non-ASCII chars, punctuation, numbers and words.

Usage: python3 inline_check.py
"""
import importlib.util
import json
import re
import sys


def load_canon():
    spec = importlib.util.spec_from_file_location("chk", "check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def skeleton(m, s):
    s = s.replace("`", "").replace("$", "")
    s = m.canon(s)
    s = s.replace("^", "").replace("R_+", "R+").replace("R+_", "R+")
    s = re.sub(r"\s+", "", s)
    return s


def main():
    m = load_canon()
    mapping = json.load(open("inline_map.json", encoding="utf-8"))
    bad = 0
    for old, new in mapping.items():
        a, b = skeleton(m, old), skeleton(m, new)
        if a != b:
            bad += 1
            print("!! skeleton mismatch")
            print("   old:", a)
            print("   new:", b)
    print("lines checked: %d  mismatches: %d" % (len(mapping), bad))
    print("RESULT:", "PASS" if bad == 0 else "FAIL")
    return 0 if bad else 1


if __name__ == "__main__":
    sys.exit(main())
