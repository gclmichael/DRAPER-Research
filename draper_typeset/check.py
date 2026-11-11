#!/usr/bin/env python3
"""Content-preservation checker for the DRAPER typesetting job.

Converting ASCII math to LaTeX legitimately rewrites *structure*:
    sqrt(x)   -> \\sqrt{x}        (braces carry the grouping)
    a/(b+c)   -> \\frac{a}{b+c}   (fraction bar carries the grouping)
    rho((a)/b)-> \\rho(\\frac{a}{b})

So we first CANONICALIZE both sides into a common ASCII form, then require the
canonical forms to agree on:
  1. CJK characters                       exact
  2. all non-ASCII characters             exact  (fullwidth<->halfwidth drift)
  3. ASCII punctuation  : , ; ! ?         exact  (separator drift)
  4. numeric literals                     exact  (0.5 must not become 1/2)
  5. identifier words                     no loss
  6. fences / $$ balanced

Usage: python3 check.py <group>
"""
import json
import re
import sys

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

GREEK = ["alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta",
         "eta", "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu",
         "xi", "pi", "rho", "sigma", "varsigma", "tau", "upsilon", "phi",
         "varphi", "chi", "psi", "omega", "Gamma", "Delta", "Theta",
         "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega"]

LATEX_ONLY = set(GREEK) | {
    "frac", "sqrt", "sum", "prod", "int", "log", "ln", "exp", "min", "max",
    "arg", "lim", "sup", "inf", "left", "right", "text", "mathrm", "mathbb",
    "mathcal", "operatorname", "top", "cdot", "times", "le", "ge", "ne",
    "sim", "approx", "propto", "notin", "forall", "exists", "nabla",
    "partial", "infty", "rightarrow", "leftarrow", "mapsto", "quad", "qquad",
    "begin", "end", "aligned", "array", "cases", "pmatrix", "bmatrix",
    "overline", "underline", "hat", "bar", "vec", "dot", "binom", "choose",
    "textstyle", "displaystyle", "limits", "colon", "mid", "vert", "circ",
    "ast", "star", "oplus", "otimes", "cup", "cap", "subset", "supset",
    "pm", "ll", "gg", "equiv", "cong", "leq", "geq", "neq", "langle",
    "rangle", "lceil", "rceil", "lfloor", "rfloor", "triangle", "ell",
    "hspace", "vspace", "space", "mathbf", "mathit", "mathsf", "mathtt",
    "underset", "overset", "stackrel", "substack", "xrightarrow",
    "xleftarrow", "iff", "implies", "longleftrightarrow", "hfill",
    "nonumber", "mathord", "mathbin", "mathrel", "bmod", "pmod", "lvert",
    "rvert", "lVert", "rVert", "Vert", "big", "Big", "bigg", "Bigg",
    "bigl", "bigr", "not", "land", "lor", "neg", "emptyset", "varnothing",
    "setminus", "bullet", "wedge", "vee", "ominus", "odot", "sqcup",
    "sqcap", "uplus", "amalg", "diamond", "diag", "rank", "tr", "det",
    "dim", "ker", "deg", "argmax", "argmin", "cov",
}

STOP = {"the", "and", "for", "with", "from", "that", "this", "only", "under",
        "given", "find", "such", "are", "is", "on", "of", "if", "in", "as"}


# named LaTeX commands -> the ASCII token they stand for
NAMED = {
    "leftarrow": "<-", "rightarrow": "->", "leftrightarrow": "<=>",
    "to": "->",
    "le": "<=", "leq": "<=", "ge": ">=", "geq": ">=", "ne": "!=", "neq": "!=",
    "cdot": "*", "times": "*", "ast": "*",
    "top": "^T", "sim": "~", "iff": "<=>", "mid": "|", "Vert": "||",
    "vert": "|", "in": " in ", "min": "min", "max": "max", "log": "log",
    "ln": "ln", "exp": "exp", "sum": "sum", "prod": "prod", "int": "int",
    "infty": "infinity", "argmin": "argmin", "argmax": "argmax",
    "diag": "diag", "grad": "grad", "var": "var", "cov": "cov",
    "det": "det", "dim": "dim", "deg": "deg", "emptyset": "0",
    "varnothing": "0", "quad": " ", "qquad": " ", "space": " ",
    "hspace": " ", "vspace": " ",
}
NAMED_RE = re.compile(r"\\(%s)(?![A-Za-z])" % "|".join(
    sorted(NAMED, key=len, reverse=True)))


def canon(s):
    """Canonicalize LaTeX/ASCII math into one common ASCII-ish form."""
    # keep the payload of \text{...} etc. (incl. \operatorname)
    for _ in range(5):
        s = re.sub(r"\\(?:text|textrm|textit|mathrm|operatorname|mathbf|mathit"
                   r"|mathsf|mathtt|mathbb|mathcal)\s*\*?\s*\{([^{}]*)\}",
                   r"\1", s)
    # \frac{A}{B} -> ((A)/(B)) ; \sqrt{A} -> sqrt(A)
    for _ in range(5):
        s = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"((\1)/(\2))", s)
    for _ in range(5):
        s = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"sqrt(\1)", s)
    # \\[4pt] line-spacing argument
    s = re.sub(r"\\\\\[[^\]]*\]", " ", s)
    # named commands (longest first, not followed by a letter)
    s = NAMED_RE.sub(lambda m: NAMED[m.group(1)], s)
    # \left / \right only when used as auto-sizing delimiters
    s = re.sub(r"\\left(?![A-Za-z])", "", s)
    s = re.sub(r"\\right(?![A-Za-z])", "", s)
    # spacing / sizing commands vanish
    s = re.sub(r"\\[,.!;:]", " ", s)
    s = s.replace("\\ ", " ")
    s = re.sub(r"\\[bB]igg?[lrm]?", "", s)
    s = s.replace("\\|", "||").replace("\\_", "_")
    s = s.replace("\\{", "{").replace("\\}", "}")
    # greek -> names
    for g in GREEK:
        s = s.replace("\\" + g, g)
    s = re.sub(r"\\[A-Za-z]+\*?", "", s)     # drop any remaining macro
    s = s.replace("$$", " ").replace("$", " ")
    s = s.replace("\\", "")
    s = re.sub(r"[{}]", "", s)               # braces are grouping only
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def cjk_multiset(s):
    d = {}
    for ch in CJK.findall(s):
        d[ch] = d.get(ch, 0) + 1
    return d


def nonascii_multiset(s):
    d = {}
    for ch in s:
        if ord(ch) > 127:
            d[ch] = d.get(ch, 0) + 1
    return d


def count_chars(s, chars):
    d = {}
    for ch in s:
        if ch in chars:
            d[ch] = d.get(ch, 0) + 1
    return d


def numbers(s):
    d = {}
    for x in re.findall(r"\d+(?:\.\d+)?", s):
        d[x] = d.get(x, 0) + 1
    return d


def words(s):
    out = set()
    for w in re.split(r"[^A-Za-z0-9_]+", s):
        for part in w.split("_"):
            part = part.strip()
            if len(part) >= 2 and part.isalpha():
                out.add(part.lower())
    return out


def cmp_maps(old, new, label, bid, bad, exact=True):
    missing = {k: old[k] - new.get(k, 0) for k in old if old[k] > new.get(k, 0)}
    extra = {k: new[k] - old.get(k, 0) for k in new if new[k] > old.get(k, 0)}
    if missing or (exact and extra):
        print("!! %s: %s mismatch" % (bid, label))
        if missing:
            print("     LOST :", missing)
        if extra:
            print("     EXTRA:", extra)
        bad[0] += 1


def check(group):
    blocks = {b["id"]: b for b in json.load(open("blocks.json", encoding="utf-8"))}
    try:
        out = json.load(open("out_%s.json" % group, encoding="utf-8"))
    except FileNotFoundError:
        print("MISSING out_%s.json" % group)
        return 1

    bad = [0]
    for bid, new in sorted(out.items()):
        if bid not in blocks:
            print("!! %s: unknown block id" % bid)
            bad[0] += 1
            continue
        old = blocks[bid]["text"]

        cmp_maps(cjk_multiset(old), cjk_multiset(new), "CJK chars", bid, bad)
        cmp_maps(nonascii_multiset(old), nonascii_multiset(new),
                 "non-ASCII chars", bid, bad)

        co, cn = canon(old), canon(new)

        # punctuation: NO additions ever. Deletions only allowed for ",;"
        # when the block was turned into a markdown bullet list (the bullet
        # becomes the item separator, replacing the trailing comma).
        po, pn = count_chars(co, ":,;!?"), count_chars(cn, ":,;!?")
        added = {k: pn[k] - po.get(k, 0) for k in pn if pn[k] > po.get(k, 0)}
        lost_p = {k: po[k] - pn.get(k, 0) for k in po if po[k] > pn.get(k, 0)}
        if added:
            print("!! %s: punctuation ADDED -> %s" % (bid, added))
            bad[0] += 1
        if lost_p:
            is_list = any(l.lstrip().startswith(("- ", "* ", "1. "))
                          for l in new.split("\n"))
            only_seps = set(lost_p) <= {",", ";"}
            if not (is_list and only_seps):
                print("!! %s: punctuation LOST -> %s (list=%s)"
                      % (bid, lost_p, is_list))
                bad[0] += 1
            else:
                print("   %s: %s absorbed by list markup (allowed)" % (bid, lost_p))

        cmp_maps(numbers(co), numbers(cn), "numeric literals", bid, bad)

        wo, wn = words(co), words(cn)
        lost = wo - wn
        if lost:
            print("!! %s: words LOST -> %s" % (bid, sorted(lost)))
            bad[0] += 1
        extra_w = {w for w in (wn - wo) if w not in LATEX_ONLY and w not in STOP}
        if extra_w:
            print("?? %s: new words -> %s  (confirm these are LaTeX commands)"
                  % (bid, sorted(extra_w)))

        if new.count("```") % 2 != 0:
            print("!! %s: unbalanced ``` fence" % bid)
            bad[0] += 1
        if new.count("$$") % 2 != 0:
            print("!! %s: unbalanced $$" % bid)
            bad[0] += 1

    if bad[0] == 0:
        print("OK  (%d blocks checked in group %s)" % (len(out), group))
        return 0
    print("FAIL  (%d problems in group %s)" % (bad[0], group))
    return 1


if __name__ == "__main__":
    sys.exit(check(sys.argv[1]))
