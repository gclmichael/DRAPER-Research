# DRAPER 排版优化规范（唯一契约）

目标：**只改排版，绝不改内容。** 让公式在 Obsidian 里正常渲染。

---

## 0. 铁律

1. **一个字的正文都不许改。** 中文、英文、数字、专有名词（`profi`、`AutoFDO`、
   `BlockFrequencyInfo`…）必须逐字保留。不许改写、不许精简、不许润色、不许补全。
2. **公式符号可以转成 LaTeX。** 这是本次唯一允许的"改写"，且只在数学表达式内部。
3. **围栏代码块之外的正文一律不动。** 由 Lead 统一装配，你只产出块内容。
4. 不许新增解释文字。原文没有的说明、小标题、注释一律不许加。
5. 不许删除分隔线 `---`、不许删标题、不许调整章节顺序。

---

## 1. 块的处理方式（四选一）

| 类型 | 判定 | 处理 |
|---|---|---|
| **MATH** | 整块就是一个或几个数学式，没有中文词条 | 转 `$$ ... $$` 行间公式 |
| **LIST** | 块是「词条 = 值」或纯枚举，**含中文词条** | 转 markdown 列表，中文原样，值用行内 `$...$` |
| **KEEP** | 伪代码、算法框、YAML、bash、ASCII 树/流程图、清单 | 保留围栏代码块（可修语言标注） |
| **PROSE** | 整块其实是普通句子，不该是代码块 | 去掉围栏，还原成普通段落 |

判定优先级：含 `Algorithm` → KEEP；`yaml`/`bash` → KEEP；
含 ASCII 树（`├`、`->` 缩进层级、`|` 竖线对齐全是图形）→ KEEP；
其余按上表。

---

## 2. MATH 写法规范

### 2.1 行间公式

```markdown
$$
J(x,\lambda) = \sum_i \rho\!\left(\frac{s_i - q_i A_i(\lambda)x}{\sigma_i}\right)
  + \frac{1}{2}(x-\mu_\theta)^\top \Sigma_\theta^{-1}(x-\mu_\theta)
  + \beta\,\Omega(\lambda)
$$
```

- `$$` 单独占一行，前后各留一个空行。
- 用 `aligned` 对齐多行：

```markdown
$$
\begin{aligned}
z &= A(\lambda)\,x + \varepsilon_{\text{transform}} \\
s_i &\sim \operatorname{NegBin}(q_i z_i,\ \alpha_i)
\end{aligned}
$$
```

### 2.2 符号对照表（强制统一）

| 原文 | LaTeX | 原文 | LaTeX |
|---|---|---|---|
| `alpha` | `\alpha` | `Sigma` | `\Sigma` |
| `beta` | `\beta` | `sigma` | `\sigma` |
| `gamma` | `\gamma` | `tau` | `\tau` |
| `delta` | `\delta` | `theta` | `\theta` |
| `epsilon` | `\epsilon` | `phi` | `\phi` |
| `eta` | `\eta` | `Omega` | `\Omega` |
| `lambda` | `\lambda` | `Pi` | `\Pi` |
| `mu` | `\mu` | `rho` | `\rho` |
| `pi` | `\pi` | `sum_i` | `\sum_i` |
| `x^T` / `^T` | `^\top` | `sqrt{..}` | `\sqrt{..}` |
| `<=` | `\le` | `>=` | `\ge` |
| `!=` | `\ne` | `<-` | `\leftarrow` |
| `||...||_1` | `\|...\|_1` | `in` | `\in` |
| `R+` | `\mathbb{R}_{+}` | `argmin` | `\operatorname*{arg\,min}` |
| `max(0,..)` | `\max(0,..)` | `log` | `\log` |
| `grad f` | `\nabla f` | `~` (分布) | `\sim` |
| `...^T Sigma^-1 ...` | `...^\top \Sigma^{-1} ...` | `*` (乘) | `\,` 或 `\cdot` |

- 多字符下标用花括号：`x_MAP` → `x_{\text{MAP}}`、`lambda_t+1` → `\lambda_{t+1}`、
  `p_flip` → `p_{\text{flip}}`、`D_risk` → `D_{\text{risk}}`、`V_IR` → `V_{\text{IR}}`。
- 语义化名字（MAP/flip/risk/IR/BIN/CTX/TEG/CFG/call/loop/sample/prior/mapping/
  safe/obj/flow/transform/unroll/body/callee/caller/cs/inlined）统一用 `\text{...}`。
- 单个字母变量直接写：`x_i`、`z_i`、`q_i`、`s_i`、`r_i`、`a_i`、`k`、`n`、`m`、`p`、`t`。

### 2.3 中文绝不能塞进公式

原文里的中文词条必须留在 Markdown 正文里：

```markdown
- 采样率 $q = 1/1000$
- 噪声 $\sigma = \sqrt{\text{sample} + 1}$
- unroll 覆盖 = 高
```

不要写成 `$$\text{采样率 } q = 1/1000$$`。

---

## 3. LIST 写法规范

原文：

```text
x = real counts on early IR entities
z = expected counts on binary entities
```

转为：

```markdown
- $x$：early IR 实体上的真实 counts —— 见下方（**保持原句**）
```

更贴切的例子（保留原句、只把变量抽成公式）：

- $x$ = real counts on early IR entities
- $z$ = expected counts on binary entities
- $s$ = observed sampled counts
- $\lambda$ = unknown transform ratios

即：**词条原样保留**，只把变量名包进 `$...$`。这样零文字损失。

---

## 4. KEEP 写法规范

- 保留围栏，语言标注按内容修正：
  - 伪代码/算法框 → ` ```text `
  - YAML → ` ```yaml `
  - shell 命令 → ` ```bash `
- 块内**一个字都不许动**，包括缩进。只允许改开头那一行的语言标注。

---

## 5. PROSE 写法规范

若整块其实是普通句子（如"还原 counts 越接近真实执行 counts，PGO 效果越好。"），
去掉围栏变成普通段落，句末标点保持不变。

---

## 6. 自检

每个块改完必须过：

```bash
python3 check.py <group>     # 例如 python3 check.py early
```

输出 `OK` 才算完成。`LOST`/`EXTRA` 必须修到干净。

---

## 7. 输出格式

产出 `out_<group>.json`，**只含被改动的块**：

```json
{
  "B006": "- 采样率 $q = 1/1000$\n- 噪声 $\\sigma = \\sqrt{\\text{sample} + 1}$\n- unroll 覆盖 = 高",
  "B066": "```text\nAlgorithm 1 DRAPER\n...\n```"
}
```

- key 是块 id，value 是**替换后的完整 Markdown 文本**（含围栏，如果有）。
- 不想改的块**不要出现在 JSON 里**，保持原样。
- value 里若含围栏，按需用 ` ```text ` 包裹。
- **JSON 必须 UTF-8、`ensure_ascii=False`。**
