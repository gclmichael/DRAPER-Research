#!/usr/bin/env python3
"""Convert clearly-mathematical inline `code` spans in prose to $...$.

Safety: only the delimiters and the ASCII->LaTeX transliteration change.
No word, digit or CJK character is added or removed (verified by inline_check.py).

Writes inline_map.json ; with --apply rewrites final.md.
With no flag, prints a full review table of every proposed change.
"""
import json
import re
import sys

GREEK = ["alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta",
         "eta", "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu",
         "xi", "pi", "rho", "sigma", "varsigma", "tau", "upsilon", "phi",
         "varphi", "chi", "psi", "omega", "Gamma", "Delta", "Theta",
         "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega"]
GSET = set(GREEK)
OPS = set("=<>^_|+*/-~")

# spans needing exact hand treatment (verified against source text)
OVERRIDES = {
    "T: R+^|V_IR| -> R+^|V_BIN|":
        r"\text{T}: \mathbb{R}_{+}^{|V_{\text{IR}}|} \to \mathbb{R}_{+}^{|V_{\text{BIN}}|}",
    "z_t = A(lambda_t) x_t": r"z_t = A(\lambda_t)\, x_t",
    "||Delta x||_1 <= budget": r"\|\Delta x\|_1 \le \text{budget}",
    "x*": r"x^*",
    "k=4": r"k=4",
    "alpha_k = 1/k": r"\alpha_k = 1/k",
    "lambda = 1/2": r"\lambda = 1/2",
    "x_0 in X": r"x_0 \in X",
    "lambda_t+1": r"\lambda_{t+1}",
    "z=A(lambda_t+1)x": r"z=A(\lambda_{t+1})x",
    "z=A(lambda_t+1) x": r"z=A(\lambda_{t+1})x",
    "y_d=g_d^T x": r"y_d=g_d^\top x",
    "margin_d(x) >= 0": r"\text{margin}_d(x) \ge 0",
    "D_risk": r"D_{\text{risk}}",
    "D_t": r"D_t",
    "L_body": r"L_{\text{body}}",
    "pi_ij": r"\pi_{ij}",
    "1-alpha": r"1-\alpha",
    "p(edge) > theta_b": r"p(\text{edge}) > \theta_b",
    "benefit / cost > theta_i": r"\text{benefit} / \text{cost} > \theta_i",
    "block frequency / entry frequency > theta_h":
        r"\text{block frequency} / \text{entry frequency} > \theta_h",
    "count(block_i) / count(block_j) > theta_l":
        r"\text{count}(\text{block}_i) / \text{count}(\text{block}_j) > \theta_l",
    "trip count > theta_u": r"\text{trip count} > \theta_u",
    "greater than theta": r"\text{greater than } \theta",
    "less than theta": r"\text{less than } \theta",
    "coverage_penalty_i": r"\text{coverage\_penalty}_i",
    "p_lambda": r"p_{\lambda}",
    "O(p + m + n)": r"O(p + m + n)",
    "O(p_lambda)": r"O(p_{\lambda})",
    "O(K * (m + nnz(T)))": r"O(K \cdot (m + \mathrm{nnz}(T)))",
    "O(nnz(C_CFG))": r"O(\mathrm{nnz}(C_{\text{CFG}}))",
    "O(|D|)": r"O(|D|)",
    "O(q * iters + nnz(T_local))": r"O(q \cdot \text{iters} + \mathrm{nnz}(T_{\text{local}}))",
    "bin = ir": r"\text{bin} = \text{ir}",
    "bin_k = alpha_k * ir": r"\text{bin}_k = \alpha_k \cdot \text{ir}",
    "bin = ir_1 + ir_2": r"\text{bin} = \text{ir}_1 + \text{ir}_2",
    "bin_1 = lambda * ir; bin_2 = (1-lambda) * ir":
        r"\text{bin}_1 = \lambda \cdot \text{ir};\ \text{bin}_2 = (1-\lambda) \cdot \text{ir}",
    "callee-entry(ctx) = sum(caller edge under ctx)":
        r"\text{callee-entry}(\text{ctx}) = \sum(\text{caller edge under ctx})",
    "bin_body_k = n_unroll * ir_body":
        r"\text{bin\_body}_k = n_{\text{unroll}} \cdot \text{ir\_body}",
    "bin = p * ir_true + (1-p) * ir_false":
        r"\text{bin} = p \cdot \text{ir\_true} + (1-p) \cdot \text{ir\_false}",
    "bin_outlined = ir_cold": r"\text{bin\_outlined} = \text{ir\_cold}",
    "hinge_flip_d": r"\text{hinge\_flip}_d",
    "T(lambda)": r"T(\lambda)",
    "Omega(lambda)": r"\Omega(\lambda)",
}


def base_token(x):
    if x in GSET:
        return "\\" + x
    if len(x) == 1:
        return x
    return "\\text{%s}" % x.replace("_", "\\_")


def sub_token(t):
    """Render a subscript, bracing only when the token needs it."""
    if t in GSET:
        return "\\" + t                       # \theta  (no braces needed)
    if len(t) == 1:
        return t                              # x_t   (no braces needed)
    return "\\text{%s}" % t.replace("_", "\\_")   # L_{\text{rank}}


def convert_ident(w):
    m = re.match(r"^(.*?)_([A-Za-z0-9]+)(\+)(\d+)$", w)   # x_t+1 -> x_{t+1}
    if m:
        return "%s_{%s+%s}" % (base_token(m.group(1)), sub_token(m.group(2)),
                               m.group(4))
    parts = w.split("_")
    if len(parts) == 1:
        return base_token(w)
    head = base_token(parts[0])
    tail = "_".join(parts[1:])
    need_brace = (tail not in GSET) and len(tail) != 1
    return "%s_{%s}" % (head, sub_token(tail)) if need_brace \
        else "%s_%s" % (head, sub_token(tail))


def conv_span(s):
    if s in OVERRIDES:
        return "$" + OVERRIDES[s] + "$"
    store = []

    def park(m):
        store.append(convert_ident(m.group(0)))
        return "\x00%d\x00" % (len(store) - 1)

    out = re.sub(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*", park, s)
    out = out.replace("<=", "\\le ").replace(">=", "\\ge ")
    out = out.replace("!=", "\\ne ").replace("<->", "\\leftrightarrow ")
    out = out.replace("->", "\\to ").replace("<-", "\\leftarrow ")
    out = out.replace("||", "\\|")
    out = re.sub(r"\s+in\s+", r" \\in ", out)
    out = re.sub(r"\b([A-Za-z0-9])\*", r"\1^*", out)
    out = out.replace("*", "\\cdot ")
    out = re.sub(r"\x00(\d+)\x00", lambda m: store[int(m.group(1))], out)
    out = re.sub(r"\s+", " ", out).strip()
    return "$" + out + "$"


def is_math_span(s):
    if not s or re.match(r"^-[A-Za-z0-9]", s):
        return False
    if s in OVERRIDES:
        return True
    if any(t in GSET for t in re.findall(r"[A-Za-z]+", s)):
        return True
    if any(c in OPS for c in s):
        return True
    if re.fullmatch(r"[A-Za-z]", s):
        return True
    return False


def process_line(line):
    if "`" not in line or "$$" in line or line.strip().startswith("|--"):
        return line
    is_table = line.lstrip().startswith("|")

    def repl(m):
        inner = m.group(1)
        if not is_math_span(inner):
            return m.group(0)
        conv = conv_span(inner)
        if is_table and "|" in conv:
            return m.group(0)
        return conv

    return re.sub(r"`([^`]+)`", repl, line)


def main():
    apply = "--apply" in sys.argv
    lines = open("assembled.md", encoding="utf-8").read().split("\n")
    out, mapping = [], {}
    infence = False
    for line in lines:
        if line.strip().startswith("```"):
            infence = not infence
            out.append(line)
            continue
        new = line if infence else process_line(line)
        if new != line:
            mapping[line] = new
        out.append(new)
    json.dump(mapping, open("inline_map.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("lines changed: %d / %d" % (len(mapping), len(lines)))
    if apply:
        open("final.md", "w", encoding="utf-8").write("\n".join(out))
        print("final.md updated")
    else:
        for k, v in mapping.items():
            print("  -", k.strip()[:150])
            print("  +", v.strip()[:150])


if __name__ == "__main__":
    main()
