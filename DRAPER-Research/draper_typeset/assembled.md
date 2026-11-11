# DRAPER：决策风险感知的采样 Profile 还原算法

**DRAPER = Decision-Risk-Aware Profile Evidence Restoration**

这个方案的目标是构造一个可发表 CCF-A 的算法模型：不是再提出一个“修复 counts 的 GNN”，而是把采样式反馈优化还原问题重新形式化为：

> 在编译器变换图上，对带噪采样证据做流一致的贝叶斯反演，并直接最小化 PGO 决策翻转造成的预期性能损失。

模型的核心可以用一句话概括：

> Profile 还原的终点不是让每个 count 看起来精确，而是让消费这些 count 的编译器决策在采样不确定性下仍然安全。

---

## 1. 从一个明确 insight 出发

现有方法的隐含假设是：

还原 counts 越接近真实执行 counts，PGO 效果越好。

这个假设不完整。编译器并不直接消费每个绝对 count，而是消费一组局部决策谓词：

1. 这个基本块是否是热点；
2. 这条边概率是否超过阈值；
3. 这个调用点的内联收益是否超过成本；
4. 这个循环是否值得展开或向量化；
5. 两个块在 layout 中谁排前面；
6. cold region 是否应该拆出去；
7. 间接调用目标是否值得提升成直调。

因此，两个 counts 误差很大的 profile 可能做出完全相同的优化决策；两个 counts 误差很小的 profile 也可能在关键阈值附近翻转内联、布局或分支概率决策。

DRAPER 的优化目标从原始误差改为决策风险：

- 传统目标: minimize $\|\text{inferred counts} - \text{true counts}\|$
- DRAPER 目标: minimize $\text{expected decision loss} = \sum\limits_{\text{over PGO predicates of}} \text{cost}(\text{flip predicate}) \cdot \text{probability}(\text{flip predicate})$

但这个目标不能脱离程序语义。DRAPER 同时强制输出满足 CFG 流守恒、调用图一致、循环约束和非负性。

---

## 2. 与现有工作的机制差异

| 方法 | 核心机制 | 遗留问题 |
|---|---|---|
| AutoFDO / llvm-profgen | 用 debug info 或伪插桩映射采样 | 映射歧义仍存在，基本输出点估计 |
| profi | 用最小费用流修复不守恒 counts | 主要修 counts，不建模变换和 PGO 决策代价 |
| CSSPGO | 伪插桩 + 上下文敏感 profile | 上下文更强，但不是决策风险感知还原 |
| BOLT / Propeller | 二进制布局或重链接优化 | 难以重新触发 IR 级内联和循环优化 |
| ProfiX | GNN 学习修复 profile | 输出 counts，缺少硬约束、不确定性和决策代价建模 |

DRAPER 的差异不是“再加一个神经网络”，而是改变问题结构：

1. 建立**编译器变换证据图**，而不是只做二进制块到 IR 块的匹配；
2. 输出**带不确定性的流一致后验**，而不是单点 counts；
3. 对齐 LLVM 中真实消费 profile 的**决策谓词**；
4. 对高风险决策做**有界修正或主动校准**；
5. 神经网络只提供先验和方差，最终 profile 必须投影回程序语义可行域。

---

## 3. 模型总览与算法实现

如果只用一段话解释 DRAPER，可以这样说：

> 先把“优化后二进制里看到的东西”和“早期 IR 里需要优化权重的东西”连成一张带编译变换语义的证据图；再把硬件采样挂到这张图上；然后反复调整 IR counts 和 clone/split/inline 比例，直到这些 counts 既解释采样，又满足 CFG 流守恒；最后不直接交付所有 counts，而是检查编译器会做的 inline、layout、branch、loop 决策，只对阈值附近可能翻错的少数决策做有界修正。

### 3.A 一分钟看懂每个阶段

| 步骤 | 一句话目标 | 具体做什么 | 输入 | 输出 |
|---|---|---|---|---|
| 1. 建图 | 知道“二进制东西”从哪些“IR 东西”变来 | 从 IR CFG、二进制 CFG、pseudo-probe、FuncHash、debug info 和 optimization remarks 中建立映射；内联建 inline 边，展开建 clone 边，合并建 merge 边，分裂建 split 边 | 源码、编译选项、优化后二进制、编译审计信息 | 变换证据图 TEG |
| 2. 挂样本 | 把运行事件放到图上 | 把 IP/branch/call 样本映射到二进制节点；估计每个节点的采样率、噪声和覆盖度；用 profi 生成一个满足 CFG 守恒的初始 IR counts | TEG、perf/LBR/ETM/SPE 样本 | 初始 counts、采样率和噪声 |
| 3. 还原 counts | 让 counts 同时解释采样和程序流 | 反复交替更新：先根据局部样本更新 clone/split/inline 比例，再固定比例更新 IR counts，然后把结果投影回流守恒平面 | TEG、样本、初始 counts | MAP 后验 counts 和不确定性 |
| 4. 找风险 | 找出真正会改变编译结果的谓词 | 从 LLVM 抽取 inline/layout/branch/loop 等阈值判断；用 counts 后验计算每个判断越过阈值的概率 | MAP counts、方差、LLVM 管线 | 高风险谓词列表 |
| 5. 安全修正 | 修正少数可能翻错的决策 | 只在受影响的局部 CFG/调用图上调 counts；保证流守恒；限制总修正量；修不动就打保守标签而不是硬改 | MAP counts、高风险谓词、修正预算 | 最终 counts、置信区间、stable/risky/unknown 标签 |

换句话说：

1. **第 1 步回答：观测和 IR 是什么关系？**
2. **第 2 步回答：观测到了多少、可信程度是多少？**
3. **第 3 步回答：在程序控制流约束下最合理的 IR counts 是什么？**
4. **第 4 步回答：哪些 counts 会真正影响编译器决策？**
5. **第 5 步回答：为了防止决策翻错，应该在哪里做多大修正？**

### 3.B 用一个小例子走一遍

假设早期 IR 中有一个函数 `callee`，一个 IR 基本块 `B3`，一个调用点 `cs1`。优化后 `callee` 被内联进 `caller` 的 `cs1`，并且原来一个循环体块 `L_body` 被展开成 4 份二进制块：

```text
IR:
    caller
      cs1 -> callee
    callee:
      B1
      B2
      B3
      loop:
        L_body

binary:
    caller:
      inlined.B1
      inlined.B2
      inlined.B3
      unroll.body.1
      unroll.body.2
      unroll.body.3
      unroll.body.4
```

#### 第 1 步建图

Evidence Agent 生成以下映射：

$$
\begin{aligned}
\text{inlined.B1} &\leftarrow \text{identity}/\text{inline}(B_1) \\
\text{inlined.B2} &\leftarrow \text{identity}/\text{inline}(B_2) \\
\text{inlined.B3} &\leftarrow \text{identity}/\text{inline}(B_3) \\
\text{unroll.body.k} &\leftarrow \text{clone}(\text{L\_body},\ k=1,2,3,4) \\
\text{caller.cs1.entry} &\leftarrow \text{inline}(\text{callee.entry},\ \text{cs1})
\end{aligned}
$$

如果 optimization remarks 明确记录了 unroll factor，`k=4` 是高置信证据；如果只有行表匹配，则 clone 边的置信度较低。

#### 第 2 步挂样本

假设 perf/LBR 样本映射后得到：

$$
\begin{aligned}
\text{inlined.B1} &: 80\ \text{samples} \\
\text{inlined.B2} &: 40\ \text{samples} \\
\text{inlined.B3} &: 20\ \text{samples} \\
\text{unroll.body.1} &: 25\ \text{samples} \\
\text{unroll.body.2} &: 30\ \text{samples} \\
\text{unroll.body.3} &: 20\ \text{samples} \\
\text{unroll.body.4} &: 25\ \text{samples}
\end{aligned}
$$

系统估计：

- 采样率 $q = 1/1000$
- 噪声 $\sigma = \sqrt{(\text{sample} + 1)}$
- unroll 覆盖 = 高

先用 profi 得到满足 IR CFG 守恒的初始 counts。例如：

$$
\begin{aligned}
B_1 &= 80000 \\
B_2 &= 40000 \\
B_3 &= 20000 \\
L_{\text{body}} &= 100000
\end{aligned}
$$

这些不是最终答案，只是可行起点。

#### 第 3 步还原

第一轮先更新变换比例。四个展开体都属于同一个 IR 块 `L_body`，当前局部样本合计：

$$
25 + 30 + 20 + 25 = 100
$$

若使用等比例先验，初始 clone weight 为：

$$
\alpha_1 = \alpha_2 = \alpha_3 = \alpha_4 = 0.25
$$

二进制期望值为：

$$
\operatorname{expected}(\text{unroll.body.k}) = q \cdot \alpha_k \cdot L_{\text{body}}
$$

然后固定比例更新 IR counts。观测显示四个展开体总和对应约：

$$
\frac{100\ \text{samples}}{q} = 100000
$$

结合展开关系：

$$
4 \cdot \alpha \cdot L_{\text{body}} = 100000
$$

若四个 alpha 均为 0.25，则：

$$
L_{\text{body}} = 100000
$$

这和 profi 初值一致，所以本例不需要大幅移动。接着系统投影检查 IR CFG：

$$
\begin{aligned}
\operatorname{sum}(\text{incoming } L_{\text{body}}) &= \operatorname{sum}(\text{outgoing } L_{\text{body}}) \\
\text{callee entry} &= \operatorname{sum}(\text{caller edge cs1})
\end{aligned}
$$

如果不一致，就只修这个函数和调用点的局部子图。

#### 第 4 步找风险

假设 LLVM 内联器查看 `cs1`：

$$
\frac{\text{inline benefit}}{\text{cost}} > \text{threshold}
$$

若当前后验是：

- callsite count $= 80000$
- margin $= \text{benefit}/\text{cost} - \text{threshold} = 0.12$
- 标准差 $= 0.11$

则这个谓词在阈值附近，属于 risky。

同样，layout pass 可能看到：

$$
\frac{\operatorname{count}(\text{inlined.B3})}{\operatorname{count}(\text{inlined.B2})} > 0.8
$$

当前估计：

- $B_3/B_2 = 0.5$
- 置信区间 $[0.35,\ 0.68]$

该谓词不跨阈值，因此是 stable。

#### 第 5 步修正

对 risky 的内联谓词，DRAPER 不会随便改所有 counts。它检查 `cs1`、`callee.entry` 和相关 caller edge 组成的局部子图，求解：

- 尽量保持原 counts $+$ 尽量把 margin 推到安全侧
- subject to:
  - caller/callee 流守恒
  - counts 非负
  - $\|\text{修正量}\|_1 \le \text{budget}$

如果小幅修正可以把 margin 稳定到安全侧，就修正这几个实体；如果需要大幅扭曲 profile 才能修正，DRAPER 不硬改，而是给该内联决策打：

$$
\begin{aligned}
\text{decision\_tag} &= \text{risky-inline} \\
\text{policy} &= \text{conservative threshold}
\end{aligned}
$$

最终输出不是单纯：

$$
B_3 = 20000
$$

而是类似：

```yaml
entity: callee.B3
count: 20000
interval: [15800, 24700]
mapping: inlined-from-callee.B3
decision_tags:
  layout: stable-cold

entity: caller.cs1
count: 80000
interval: [71000, 89000]
decision_tags:
  inline: risky-hot
  policy: conservative
```

这个例子的关键在于：**还原误差本身不是终点；`cs1` 的 count 会让 LLVM 做一个靠近阈值的内联判断，所以系统最后处理的是这个决策风险。**

### 3.C 正式算法视图

下面仍保留论文需要的正式定义。如果读者只关心机制，读完 3.A 和 3.B 即可；如果关心算法正确性和实现，继续看 3.0 之后的小节。

DRAPER 将采样 profile 还原建模为一个分阶段约束推断过程。输入为：

$$
I = (S,\ F,\ B,\ R)
$$

其中 `S` 是源程序，`F` 是编译选项，`B` 是优化后二进制，`R` 是硬件采样记录。DRAPER 的输出不是单个频次向量，而是四元组：

$$
O = (x^*,\ U,\ T,\ A)
$$

其中：

1. `x*` 是早期 IR 上的频次向量；
2. `U` 是频次后验置信区间；
3. `T` 是 PGO 决策稳定性标签；
4. `A` 是结构和证据审计报告。

算法由五个阶段组成：证据图构建、证据聚合与初始化、流一致推断、决策风险分析、有界决策修正。它们依次执行，共享同一组状态变量和不变量。工程实现可以给每个阶段一个 typed-agent 包装，但数学上不是五个独立模型的拼盘。

全局状态定义为：

$$
H_t = (G,\ E,\ w,\ x_t,\ \lambda_t,\ z_t,\ \Sigma_t,\ D_t)
$$

| 符号 | 含义 |
|---|---|
| `G` | 早期 IR 的函数 CFG、调用图和循环结构 |
| `E` | 变换证据图 TEG |
| `w` | 采样观测向量和噪声权重 |
| `x_t` | 第 `t` 次迭代时的 IR 频次向量 |
| `lambda_t` | 克隆、分裂、内联等变换比例向量 |
| `z_t` | 二进制实体上的期望频次向量 |
| `Sigma_t` | 频次协方差或对角方差估计 |
| `D_t` | 从 LLVM 管线抽取的 PGO 决策谓词集合 |

算法运行中始终保持三条不变量：

1. **语义可行性**：`x_t` 满足 CFG 流守恒、调用图一致和循环约束；
2. **变换一致性**：`z_t = A(lambda_t) x_t`；
3. **有界修正**：决策修正阶段满足 `||Delta x||_1 <= budget`。

如果证据不足以维持唯一解，DRAPER 不强行输出唯一 counts，而是扩大对应实体方差，标记为 `unknown`，或在允许部署的场景中触发稀疏校准。

### 3.0 形式化问题定义

令早期 IR 实体集合为 $V_{IR}$，二进制观测实体集合为 $V_{BIN}$。DRAPER 需要求解如下逆问题：

$$
\begin{aligned}
\text{given observations}\quad & r \in \mathbb{R}_{+}^{|V_{\text{BIN}}|} \\
\text{find}\quad & x \in \mathbb{R}_{+}^{|V_{\text{IR}}|} \\
\text{such that}\quad & x \text{ is flow-consistent on IR} \\
\text{and}\quad & r \text{ is statistically explained by } T(x) \\
\text{and}\quad & \text{PGO predicates consumed from } x \text{ are decision-stable}
\end{aligned}
$$

其中 `T: R+^|V_IR| -> R+^|V_BIN|` 是由 TEG 决定的变换算子。给定变换参数 `lambda`，该算子可写成稀疏矩阵：

$$
z = T(\lambda)\, x
$$

`z_i` 是二进制实体 `i` 的期望频次，`r_i` 是观测样本数。可行频次集合定义为：

$$
\begin{aligned}
X = \{\, x \mid\ & x \ge 0, \\
& C_{\text{CFG}}\,x = 0, \\
& C_{\text{call}}\,x = 0, \\
& C_{\text{loop}}\,x \le 0 \,\}
\end{aligned}
$$

因此 DRAPER 的优化问题是：

$$
\min_{x \in X,\ \lambda \in \Lambda}\ \
  L_{\text{sample}}\big(T(\lambda)x,\ r\big)
  + L_{\text{prior}}(x)
  + L_{\text{transform}}(\lambda)
$$

后续五个阶段分别解决这个问题的三个部分：

1. 阶段 1 构造 `T(lambda)` 的结构；
2. 阶段 2 构造可行初始点；
3. 阶段 3 在 `X` 上交替估计 `x` 和 `lambda`；
4. 阶段 4 识别会被编译器消费的风险谓词；
5. 阶段 5 在 `X` 内做有界决策修正。

### 3.1 阶段 1：构建变换证据图

该阶段把源码、编译管线和二进制统一到一个可推断的数据结构。

输入：

- source $S$, flags $F$, optimized binary $B$
- optimization remarks $Y$
- pseudo-probe metadata $M$
- debug info $L$
- FuncHash $K$

步骤：

1. 以 `-O0` 或保存的早期优化快照恢复 IR CFG 和调用图 `G`；
2. 反汇编 `B` 恢复二进制 CFG；
3. 用 pseudo-probe、FuncHash、行表和 optimization remarks 生成实体映射候选；
4. 根据 inline、clone、merge、split、loop-unroll、predication、outlining 创建变换边；
5. 对无法唯一映射的实体加入 ambiguity 节点；
6. 记录每个实体的证据来源、置信度和缺失状态。

输出：

$$
\text{TEG}\ \ E = (V_{\text{IR}},\ V_{\text{BIN}},\ V_{\text{CTX}},\ E_T)
$$

阶段结束后得到静态结构。此时还没有频次，只有“哪些二进制观测能够合法解释早期 IR 执行”的关系。

构建时每个映射边应写成稀疏变换模板：

```text
template identity(i,j):
    T[b_i, x_j]             <- 1

template clone(i,j,k):
    T[b_i, x_j]             <- alpha_k

template merge(i,j1,j2):
    T[b_i, x_j1]            <- 1
    T[b_i, x_j2]            <- 1

template split(i,j):
    T[b_i1, x_j]            <- lambda_i
    T[b_i2, x_j]            <- 1-lambda_i

template loop-unroll(j,k):
    for q = 1,...,k:
        T[b_body_q, x_body] <- n_unroll

template inline(callee, callsite, ctx):
    T[b_callee_entry(ctx), e_callsite] <- 1
```

Evidence Agent 最后输出三个可校验对象：

1. 稀疏矩阵骨架 `T0`；
2. 可学习/可估计参数集合 `Lambda`；
3. 证据属性表 `E_attr`。

### 3.2 阶段 2：证据聚合与初始化

该阶段把采样记录挂载到 TEG，并构造语义可行的初始频次。

输入：

- TEG $E$
- sample records $R$
- PMU event type
- branch/call stack availability

步骤：

1. 将 IP、branch 和 call-stack 样本按 TEG 映射边分配到二进制实体；
2. 对多候选映射，按证据置信度加权，或显式创建 split 变量；
3. 估计每个实体的采样率 `q_i`、噪声尺度 `sigma_i` 和覆盖惩罚 `coverage_penalty_i`；
4. 用 LLVM profi 或最小费用流求初始频次 `x_0`；
5. 初始化变换比例：
   - clone weight `alpha_k = 1/k`；
   - split ratio `lambda = 1/2`；
   - inline context share 按调用点静态权重初始化；
6. 初始化方差：

$$
\Sigma_0 = \operatorname{diag}(\alpha_s + s_i + \text{coverage\_penalty}_i)
$$

其中 `alpha_s` 是负二项分布的过离散参数。初始解必须已经投影回流守恒可行域，后续阶段不离开可行域。

初始化的投影问题显式写成：

$$
x_0 \leftarrow \operatorname{argmin}_{x \in X}\ \|x - \text{profi}(r, G)\|_1
$$

如果 profi 失败或不可用，则用逐函数最大熵流估计替代：

$$
x_0 \leftarrow \operatorname{argmin}_{x \in X}\ \mathrm{KL}(x \,\|\, \text{uniformPrior})
$$

这一步保证 `x_0 in X`。

输出：

initial state $(E,\ w,\ x_0,\ \lambda_0,\ z_0,\ \Sigma_0)$

### 3.3 阶段 3：流一致推断

该阶段联合更新变换比例、二进制期望频次和 IR 后验。

输入：

- state after Phase 2
- observations $s$
- sampling rates $q$
- noise scales $\sigma$

优化目标为：

$$
\begin{aligned}
J(x, \lambda)
  &= \sum_i \rho\!\left(\frac{s_i - q_i\, A_i(\lambda)\,x}{\sigma_i}\right)
   + 0.5\,(x - \mu_\theta)^\top \Sigma_\theta^{-1} (x - \mu_\theta)
   + \beta\,\Omega(\lambda) \\\\
\text{subject to:}\quad
  & C_{\text{CFG}}\,x = 0 \\
  & C_{\text{call}}\,x = 0 \\
  & C_{\text{loop}}\,x \le 0 \\
  & x \ge 0
\end{aligned}
$$

`rho` 是 Huber 损失；`mu_theta` 和 `Sigma_theta` 是图先验网络预测的均值和方差；`Omega(lambda)` 惩罚过碎或无证据的变换比例。求解使用块坐标下降。

将数据项展开为局部负对数似然：

$$L_{\text{sample}}(z, r) = \sum_i \left[\, z_i - r_i \log z_i
  + \alpha_i \log\!\left(1 + \frac{z_i}{\alpha_i}\right) \right]$$

实际实现可使用 Huber 近似：

$$
L_{\text{sample}}(z, r) = \sum_i \rho\!\left(\frac{q_i z_i - r_i}{\sigma_i}\right)
$$

变换先验取为：

$$
\Omega(\lambda) = \sum_{(i,j)} \eta_{ij}\,\mathrm{KL}(\lambda_i \,\|\, \pi_{ij})
$$

其中 `pi_ij` 是静态证据给出的比例先验。若没有证据，则 `pi_ij` 为均匀分布。

#### Step 3.1 更新变换比例

固定 `x_t`，对每组可分离变换边求解：

$$
\lambda_{t+1} \leftarrow \operatorname{argmin}_{\lambda}\ J(\lambda;\, x_t)
$$

具体策略：

1. 一个 IR 块分裂成两个二进制块时，按两个局部样本桶更新 split ratio；
2. 多个 clone 共享同一 IR 块时，按样本量和证据置信度更新 clone weight；
3. 内联上下文比例用 caller edge 的局部观测更新 callee entry 分配；
4. 更新后把比例截断到 `[epsilon, 1-epsilon]`，避免零比例永久关闭合法路径。

当变换边局部目标为二次可微时，用投影牛顿步：

$$
\lambda \leftarrow \Pi_{\Lambda}\!\left(\lambda - H_{\lambda}^{-1}\,\operatorname{grad}_{\lambda} J\right)
$$

否则用坐标搜索。`Pi_Lambda` 表示单纯形或箱式约束投影。

#### Step 3.2 更新 IR 频次

固定 `lambda_t+1`，令 `z=A(lambda_t+1)x`，求解：

$$
\begin{aligned}
\min_x\ & \sum_i \rho\!\left(\frac{s_i - q_i z_i}{\sigma_i}\right)
   + 0.5\,(x - \mu_\theta)^\top \Sigma_\theta^{-1} (x - \mu_\theta) \\\\
\text{subject to:}\quad
  & C_{\text{CFG}}\,x = 0 \\
  & C_{\text{call}}\,x = 0 \\
  & C_{\text{loop}}\,x \le 0 \\
  & x \ge 0
\end{aligned}
$$

约束按局部性拆分求解：

1. 函数内 CFG 流守恒用投影梯度或局部最小费用流；
2. 调用图一致性对 callee entry 与 caller edge 做块级修正；
3. 循环约束在 loop header/latch/exit 局部子图中投影。

每次更新后执行：

$$
x_{t+1} \leftarrow \text{ProjectToFlowManifold}(x_{t+1})
$$

投影算子定义为：

$$
\text{ProjectToFlowManifold}(y) = \operatorname{argmin}_{x \in X}\ \|x - y\|_D
$$

其中 `D` 是对角权重矩阵，观测充足的实体权重更大：

$$
D_{ii} = \frac{1}{1 + r_i + \text{coverage}_i}
$$

对单个函数的流守恒投影可用非负最小二乘；对调用图，只修正 callee entry 与 caller edge 的局部不平衡；对循环，只在包含 header、latch、exit 的子图上修正。修正必须满足子图进出流量不变，因此不会破坏已投影的其他函数。

#### Step 3.3 更新不确定性

对 MAP 解做局部 Laplace 或 blockwise bootstrap：

$$
\Sigma_{t+1} = H^{-1} + \Sigma_{\text{prior}} + \Sigma_{\text{mapping}}
$$

`H` 是数据项 Hessian 近似；`Sigma_prior` 来自图先验网络；`Sigma_mapping` 随映射歧义增大。

实际实现可先使用对角近似：

$$\Sigma_{ii} = \frac{\sigma_i^2}{q_i^2 + \delta}
  + \Sigma_{\text{prior}\_ii}
  + \text{ambiguity}_i \cdot \sigma_{\text{map}}^2$$

只有位于高风险谓词中的少量实体才需要协方差块估计。

#### 收敛条件

若同时满足：

$$
\begin{aligned}
\frac{|J_t - J_{t+1}|}{\max(1,\ |J_t|)} &< \eta_{\text{obj}} \\
\frac{\|x_{t+1} - x_t\|_1}{\max(1,\ \|x_t\|_1)} &< \eta_x \\
\max\ \text{residual}(C_{\text{CFG}}\,x,\ C_{\text{call}}\,x) &< \eta_{\text{flow}}
\end{aligned}
$$

则停止；否则最多执行 `T_max` 轮。输出 MAP 状态：

$$
(x_{\text{MAP}},\ \lambda_{\text{MAP}},\ \Sigma_{\text{MAP}})
$$

### 3.4 阶段 4：PGO 决策风险分析

该阶段定位真正会影响编译输出的谓词。

输入：

- IR module
- target LLVM pipeline $P$
- MAP posterior $(x_{\text{MAP}},\ \Sigma_{\text{MAP}})$

步骤：

1. 从 LLVM 管线静态抽取 profile 谓词：

$$d = \big(\ \text{predicate function } f_d,\ \
  \text{threshold } \theta_d,\ \
  \text{direction } \text{dir}_d,\ \
  \text{conservative fallback},\ \
  \text{aggressive action}\ \big)$$

2. 将后验投影到谓词值：

$$
\begin{aligned}
y_d &= f_d(x_{\text{MAP}}) \\
\operatorname{var}(y_d) &= \operatorname{grad} f_d^\top\ \Sigma_{\text{MAP}}\ \operatorname{grad} f_d
\end{aligned}
$$

对线性谓词 `y_d=g_d^T x`：

$$
\operatorname{var}(y_d) = g_d^\top\, \Sigma_{\text{MAP}}\, g_d
$$

3. 计算翻转概率：

$$p_{\text{flip}}(d) = \begin{cases}
  P(y_d \le \theta_d) & \text{if } \text{dir}_d = \text{greater} \\
  P(y_d \ge \theta_d) & \text{if } \text{dir}_d = \text{less}
\end{cases}$$

4. 对谓词计算风险：

$$
\text{risk}(d) = c_d \, p_{\text{flip}}(d)
$$

`c_d` 可由静态收益、code-size 增量和离线决策回放学习。

论文中应显式定义决策裕量：

$$\text{margin}_d(x) = \begin{cases}
  \theta_d - f_d(x) & \text{if } \text{dir}_d = \text{greater} \\
  f_d(x) - \theta_d & \text{if } \text{dir}_d = \text{less}
\end{cases}$$

则：

$$
\begin{aligned}
\text{stable}(d) &\iff \text{margin}_d(x_{\text{MAP}}) > k \sqrt{\operatorname{var}(y_d)} \\
\text{risky}(d) &\iff \text{otherwise}
\end{aligned}
$$

`k` 是置信系数，例如 1.96 对应约 95% 置信度。

输出高风险谓词集合：

$$
D_{\text{risk}} = \{\, d \mid p_{\text{flip}}(d) \ge \tau_p \ \text{ and } \ c_d \ge \tau_c \,\}
$$

未进入 `D_risk` 的谓词直接接受 MAP 解；进入集合的谓词交给阶段 5。

### 3.5 阶段 5：有界决策修正

该阶段只修改少数高风险实体，不整体重写 profile。

输入：

- $x_{\text{MAP}}$
- $\Sigma_{\text{MAP}}$
- $D_{\text{risk}}$
- correction budget $\epsilon$

求解：

$$
\begin{aligned}
\min_{\delta_x}\ & 0.5\,(x_{\text{MAP}} + \delta_x - \mu_\theta)^\top
   \Sigma_\theta^{-1} (x_{\text{MAP}} + \delta_x - \mu_\theta) \\
  &\quad + \gamma \sum_{d \in D_{\text{risk}}} c_d\,
   \max\!\left(0,\ \text{margin}_d(x_{\text{MAP}} + \delta_x) + \tau_{\text{safe}}\right)^2 \\\\
\text{subject to:}\quad
  & C_{\text{CFG}}\,(x_{\text{MAP}} + \delta_x) = 0 \\
  & C_{\text{call}}\,(x_{\text{MAP}} + \delta_x) = 0 \\
  & C_{\text{loop}}\,(x_{\text{MAP}} + \delta_x) \le 0 \\
  & x_{\text{MAP}} + \delta_x \ge 0 \\
  & \|\delta_x\|_1 \le \epsilon
\end{aligned}
$$

`margin_d(x) >= 0` 表示谓词处于错误侧或阈值安全裕量之内。若无法把谓词推到安全侧，DRAPER 不硬翻转，而是降低该谓词置信度并触发保守策略。

为了降低非凸性，对线性谓词可使用连续 hinge：

$$
\phi_d(x) = \max\!\left(0,\ \text{margin}_d(x) + \tau_{\text{safe}}\right)^2
$$

其中 `tau_safe` 是阈值安全裕量。修正子问题可在 `D_risk` 中按连通子图分解；只有共享 TEG 邻居或 CFG 约束的谓词才需要联合求解。

最终输出：

$$
\begin{aligned}
x^* &= x_{\text{MAP}} + \delta_x^* \\
U &= \text{intervals derived from } \Sigma(x^*) \\
T &= \text{decision stability tags} \\
A &= \text{audit report}
\end{aligned}
$$

### 3.6 论文算法框

```text
Algorithm 1 DRAPER

Input:
    source S, flags F, binary B, samples R,
    audit metadata (Y, M, L, K),
    prior parameters theta,
    thresholds eta_obj, eta_x, eta_flow,
    confidence coefficient k,
    safe margin tau_safe,
    correction budget epsilon

Output:
    flow-consistent profile x*,
    uncertainty U,
    decision tags T,
    audit report A

1:  (T0,Lambda,E_attr) <- BuildTEG(S,F,B,Y,M,L,K)
2:  (q,sigma,x0,lambda0,Sigma0) <- AggregateAndInitialize(R,T0,E_attr)
3:  x0 <- ProjectToFlowManifold(x0)
4:  t <- 0
5:  repeat
6:      lambda_t+1 <- UpdateTransformRatios(
                        x_t, lambda_t, T0, r, q, sigma, pi
                    )
7:      z_t+1 <- T(lambda_t+1) x_t
8:      x_t+1 <- SolveConstrainedMAP(
                  z_t+1, r, q, sigma, mu_theta, Sigma_theta
               )
9:      x_t+1 <- ProjectToFlowManifold(x_t+1)
10:     Sigma_t+1 <- EstimateUncertainty(
                      x_t+1, r, q, sigma, theta
                   )
11:     t <- t + 1
12: until converged or t = T_max
13: x_MAP <- x_t; lambda_MAP <- lambda_t; Sigma_MAP <- Sigma_t
14: D <- ExtractPGOPredicates(P)
15: D_risk <- SelectRiskyPredicates(
               x_MAP, Sigma_MAP, D, k
            )
16: delta_x <- SolveBoundedRiskProjection(
                x_MAP, mu_theta, Sigma_theta,
                D_risk, tau_safe, epsilon
             )
17: x* <- ProjectToFlowManifold(x_MAP + delta_x)
18: (U,T,A) <- EmitProfileAndAudit(
                x*, Sigma_MAP, D, T0, E_attr
             )
19: return (x*,U,T,A)
```

对应关系是：阶段 1 和阶段 2 为 Evidence Agent，阶段 3 为 Solver Agent，阶段 4 为 Critic Agent，阶段 5 和审计为 Projector Agent 与 Auditor Agent。论文主体应把 DRAPER 描述成一个确定性算法；typed-agent 只作为工程实现命名。

### 3.7 复杂度与实现复杂度控制

设：

$$
\begin{aligned}
n &= |V_{\text{IR}}| \\
m &= |V_{\text{BIN}}| \\
p &= |E_T| \\
q &= |D_{\text{risk}}|
\end{aligned}
$$

主要复杂度如下：

| 操作 | 复杂度 | 控制方法 |
|---|---|---|
| TEG 构建 | `O(p + m + n)` | 一次编译审计，离线缓存 |
| 变换比例更新 | `O(p_lambda)`，`p_lambda` 是比例边数 | 按变换组分解 |
| 频次 MAP | `O(K * (m + nnz(T)))`，`K` 是迭代数 | 函数级并行，只对含样本子图迭代 |
| 流投影 | `O(nnz(C_CFG))` | 只投影受修正影响的局部子图 |
| 风险谓词抽取 | `O(|D|)` | 在编译管线中增量收集 |
| 风险投影 | `O(q * iters + nnz(T_local))` | `q` 通常远小于 `|D|` |

生产实现不应构造稠密 `T`。TEG 只保存稀疏边模板；频次更新时按边流式累加。

---

## 4. 核心数据结构：变换证据图

### 4.1 为什么普通 CFG 不够

普通 profile inference 通常只处理一个函数的 CFG：

binary block $b$ has sampled count $s_b$

但赛题的真实困难是，二进制观测实体和早期 IR 实体之间存在编译器变换：

1. 被调函数被内联到多个 caller；
2. 同一 IR block 被克隆成多个二进制 block；
3. 多个 IR block 被合并成一个二进制 block；
4. 循环体被展开成多个 body copy；
5. 分支被 if-conversion 或 predication 改写；
6. cold block 被外置；
7. 优化后某些 IR block 没有直接二进制对应。

因此 DRAPER 构造异构图：

$$
\text{TEG} = (V_{\text{IR}},\ V_{\text{BIN}},\ V_{\text{CTX}},\ E_{\text{TRANSFORM}})
$$

### 4.2 节点类型

#### IR 节点

```text
IR function
IR basic block
IR CFG edge
loop header / latch / exit
call site
indirect-call target slot
```

#### 二进制节点

```text
binary function
binary basic block
branch edge
inlined clone
outlined cold block
specialized version
```

#### 上下文节点

```text
caller-context(caller, callsite, callee)
recursive context
clone version
specialization version
```

### 4.3 变换边

TEG 中的边不是普通 CFG 边，而是带变换语义的映射边。

| 变换 | 关系 | 说明 |
|---|---|---|
| identity | `bin = ir` | 一一对应 |
| clone | `bin_k = alpha_k * ir` | 同一 IR 实体克隆成多份 |
| merge | `bin = ir_1 + ir_2` | 多个 IR block 合并 |
| split | `bin_1 = lambda * ir; bin_2 = (1-lambda) * ir` | 拆分比例未知 |
| inline | `callee-entry(ctx) = sum(caller edge under ctx)` | 被调函数按上下文进入 caller |
| loop-unroll | `bin_body_k = n_unroll * ir_body` | 展开体共享 IR 循环体频次 |
| predication | `bin = p * ir_true + (1-p) * ir_false` | 分支被转成可选控制流 |
| outlining | `bin_outlined = ir_cold` | 冷代码外置 |
| elimination | 无直接二进制实体 | 观测缺失，不等于零执行 |

每条变换边保存属性：

```yaml
kind: inline
source_ir: callee.entry
target_binary: caller.0x401310
pass: inliner
confidence: 0.94
evidence:
  - optimization-remark
  - pseudo-probe
ambiguity: low
```

### 4.4 证据来源

Evidence Agent 不做自然语言推理，而是组合四类可复现证据：

1. **LLVM optimization remarks**
   - inline / no-inline；
   - loop unroll；
   - vectorization；
   - jump threading；
   - block merge/split；
   - hot/cold split。

2. **pseudo-probe 或 BasicBlockSections 元数据**
   - 不生成真实计数器；
   - 提供稳定 IR 到二进制锚点；
   - 与 LLVM CSSPGO 的机制兼容。

3. **静态指纹**
   - FuncHash；
   - CFG topology hash；
   - line table；
   - discriminator；
   - symbol、section 和 relocation。

4. **运行时采样**
   - IP sample；
   - branch stack；
   - call stack；
   - Intel LBR；
   - ARM ETM；
   - ARM BRBE；
   - ARM SPE aggregate event。

Mapper 对每个实体输出覆盖状态：

```text
covered-by-pseudo-probe
covered-by-debug-info
covered-by-cfg-hash
covered-by-transitive-transform
ambiguous-many-to-one
ambiguous-one-to-many
missing
stale-hash
```

这个状态后续直接决定样本权重、方差和是否需要校准。

---

## 5. 概率流推断

### 5.1 变量定义

令：

- $x$ = real counts on early IR entities
- $z$ = expected counts on binary entities
- $s$ = observed sampled counts
- $\lambda$ = unknown transform ratios
- $q_i$ = effective sampling rate of entity $i$
- $\sigma_i$ = observation noise scale
- $a_i$ = mapping ambiguity score

TEG 给出近似线性关系：

$$
z = A(\lambda) \cdot x + \epsilon_{\text{transform}}
$$

`A(lambda)` 是由 identity、clone、merge、split、inline、loop-unroll 等边组成的变换矩阵。

### 5.2 采样似然

硬件采样不是精确计数器。对每个二进制观测实体使用负二项分布：

$$
s_i \sim \operatorname{NegBin}(q_i \cdot z_i,\ \alpha_i)
$$

相比 Poisson，负二项更适合建模样本过离散和 PMU 突发偏差。

训练和求解中使用稳健损失：

$$
\rho(r) =
\begin{cases}
0.5 \cdot r^2 & \text{if } |r| \le \delta \\
\delta \cdot |r| - \delta^2 / 2 & \text{if } |r| > \delta
\end{cases}
$$

这样少量 PMU skid、调度异常和中断延迟不会被强行解释成真实控制流变化。

### 5.3 硬约束

最终 profile 必须满足程序语义约束。

#### CFG 流守恒

对每个非入口、非出口 IR 节点 `v`：

$$
\sum(\text{incoming edge counts}) = \sum(\text{outgoing edge counts})
$$

入口和出口用虚拟 source/sink 处理。

#### 调用一致性

对每个被调函数 `g` 和上下文 `c`：

$$
\text{entry}(g, c) = \sum(\text{caller edge counts under context } c)
$$

如果没有上下文采样，则放松为函数级：

$$
\text{entry}(g) = \sum(\text{all caller edge counts})
$$

但仍需防止函数 entry count 远小于所有 caller edge count 之和。

#### 循环约束

对循环 `L`：

$$
\begin{aligned}
\text{header count} &\ge \text{latch count} \\
\text{exit count} &\le \text{header count} \\
\sum(\text{exit edge counts}) &\le \text{header count}
\end{aligned}
$$

如果有 trip-count 证据，则加入软约束。

#### 非负约束

$$
\begin{aligned}
x &\ge 0 \\
0 \le \lambda &\le 1
\end{aligned}
$$

---

## 6. 图先验神经网络

神经组件不直接输出最终 profile，而是预测 MAP 推断需要的先验。

### 6.1 输入

对每个 IR/binary 实体抽取：

1. IR CFG 邻接；
2. binary CFG 邻接；
3. TEG 变换边；
4. sampled count；
5. sampling rate；
6. mapping coverage；
7. transformation pass tags；
8. loop depth；
9. call depth；
10. hash staleness；
11. block 指令数和调用数。

模型用轻量异构图网络：

```text
GNN encoder over TEG
  -> node embedding
  -> predict prior mean mu
  -> predict prior variance sigma
  -> predict ambiguity a
  -> predict transform ratio prior lambda
```

### 6.2 为什么不让网络直接输出 counts

如果神经网络直接输出 counts，至少有三个问题：

1. 输出可能违反 CFG 流守恒；
2. 调用图一致性会被破坏；
3. 泛化失败时没有明确的失败信号。

因此 DRAPER 采用：

GNN predicts prior and uncertainty
MAP solver predicts flow-consistent profile

这比黑盒回归更可靠，也更容易通过消融解释每个组件的贡献。

### 6.3 训练损失

训练数据是配对的 exact profile 和 sampled profile：

$$
(\text{source},\ \text{flags},\ \text{exact IR profile},\ \text{optimized binary},\ \text{sampled profile})
$$

先验网络损失：

$$
\begin{aligned}
L_{\text{prior}} &= \operatorname{L1}(\mu,\ \text{true counts}) \\
&\quad + \gamma_1 \cdot L_{\text{rank}} \\
&\quad + \gamma_2 \cdot L_{\text{threshold}} \\
&\quad + \gamma_3 \cdot L_{\text{decision}}
\end{aligned}
$$

其中：

1. `L_rank` 保证热点排序；
2. `L_threshold` 保证阈值附近决策；
3. `L_decision` 惩罚最终 PGO 谓词翻转。

训练时可以把 ground-truth profile 注入 LLVM，记录真实内联和布局决策，从而得到 decision label。

---

## 7. Solver Agent：流一致 MAP

第一阶段的 MAP 问题写成：

$$
\begin{aligned}
&\text{minimize over } x, \lambda: \\
&\qquad \sum_i \rho\left(\left(s_i - q_i \cdot z_i\right) / \sigma_i\right) + R_\theta(x, \lambda) \\
&\text{subject to:} \\
&\qquad z = A(\lambda) \cdot x \\
&\qquad \text{CFG flow conservation} \\
&\qquad \text{call graph consistency} \\
&\qquad \text{loop constraints} \\
&\qquad x \ge 0
\end{aligned}
$$

其中图先验项为：

$$
\begin{aligned}
R_\theta &= 0.5 \cdot (x - \mu)^\top \Sigma^{-1} (x - \mu) \\
&\quad + \beta \cdot \Omega(\lambda)
\end{aligned}
$$

求解策略分四步。

### 7.1 初始化

先用现有 profi 或简单最大流初始化 `x0`。

好处：

1. 起点已经满足基本 CFG 守恒；
2. 后续优化只需处理 TEG 修正；
3. 保证 DRAPER 最坏情况下退化到强 baseline。

### 7.2 变换比例估计

对每类比例变量 `lambda`：

1. clone weight；
2. split ratio；
3. predication probability；
4. inline context share；
5. loop body share。

用局部坐标下降：

```text
for each transform edge group:
    fix x
    solve small convex subproblem for lambda
    update z
```

### 7.3 counts 更新

固定 `lambda` 后，求解带线性约束的凸 MAP：

$$
\begin{aligned}
&\operatorname{min}\ \text{robust data loss} + \text{Gaussian prior} \\
&\text{subject to } \text{CFG/call/loop constraints}
\end{aligned}
$$

实现上可用：

1. projected gradient；
2. ADMM；
3. 局部 min-cost-flow 修正；
4. 大函数先分块，再跨函数融合。

### 7.4 局部重投影

每次更新后把 `x` 投影回可行域：

$$
x \leftarrow \operatorname{project\_to\_flow\_manifold}(x)
$$

这一步保证输出永远不是违反 CFG 的伪 profile。

---

## 8. Critic Agent：抽取真正重要的 PGO 谓词

这是 DRAPER 最关键的新机制。

### 8.1 从 LLVM 中抽谓词

对每个 profile 消费点抽取谓词：

$$
d = \left(
\begin{aligned}
&\text{predicate\_function } f_d, \\
&\text{threshold } \theta_d, \\
&\text{direction}, \\
&\text{cost\_false\_positive}, \\
&\text{cost\_false\_negative}
\end{aligned}
\right)
$$

第一版只覆盖五个高收益 pass：

| PGO pass | 谓词 |
|---|---|
| branch probability | `p(edge) > theta_b` |
| inliner | `benefit / cost > theta_i` |
| BlockFrequencyInfo | `block frequency / entry frequency > theta_h` |
| code layout | `count(block_i) / count(block_j) > theta_l` |
| loop unroll/vectorization | `trip count > theta_u` |

后续可扩展：

1. indirect call promotion；
2. hot/cold splitting；
3. function layout；
4. partial inline；
5. specialization。

### 8.2 谓词翻转概率

对 MAP 解 `x*` 做局部 Laplace 或 bootstrap：

$$
x \sim \operatorname{Normal}(x^*, \Sigma_x)
$$

对线性谓词：

$$
y_d = f_d^\top x
$$

近似分布：

$$
y_d \sim \operatorname{Normal}(f_d^\top x^*,\ f_d^\top \Sigma_x f_d)
$$

若方向是 `greater than theta`：

$$
p_{\text{flip}} = P(y_d \le \theta)
$$

若方向是 `less than theta`：

$$
p_{\text{flip}} = P(y_d \ge \theta)
$$

置信区间不跨阈值时，谓词是 stable。

跨阈值时，谓词是 risky。

---

## 9. Projector Agent：决策风险投影

得到 `x*` 后，DRAPER 不大改 profile，而是求解一个有界修正问题。

### 9.1 目标

$$
\begin{aligned}
&\text{minimize over } \delta_x: \\
&\qquad 0.5 \cdot (x^* + \delta_x - \mu)^\top \Sigma^{-1} (x^* + \delta_x - \mu) \\
&\qquad + \gamma \cdot \text{sum over risky } d \text{ of } c_d \cdot \text{hinge\_flip}_d(x^* + \delta_x) \\
&\text{subject to:} \\
&\qquad \text{CFG flow conservation still holds} \\
&\qquad \text{call graph consistency still holds} \\
&\qquad x^* + \delta_x \ge 0 \\
&\qquad \| \delta_x \|_1 \le \text{budget}
\end{aligned}
$$

其中：

1. `D_risk` 是阈值附近的谓词集合；
2. `c_d` 是谓词翻转的估计代价；
3. `hinge_flip_d` 惩罚向错误侧穿越阈值；
4. `budget` 防止 profile 被任意改写。

### 9.2 决策代价 `c_d`

代价不靠人工拍脑袋，可以从三类信号估计：

1. **静态收益**
   - inline callee size；
   - callsite frequency；
   - branch fallthrough benefit；
   - loop trip count；
   - code layout distance。

2. **离线编译回放**
   - 用 exact profile 得到 baseline decision；
   - 在验证集学习不同谓词翻转的 runtime/code-size 影响。

3. **在线反馈**
   - 大规模部署时可记录优化决策版本；
   - 用后续采样性能校准代价。

第一版论文可以只用静态收益和离线回放。

### 9.3 输出不是裸 profile

最终 profile entry 形如：

```yaml
block: bb17
count: 1032
posterior_interval: [790, 1310]
mapping_evidence: pseudo-probe + inline-context
ambiguity: low
consumed_by:
  - inline
  - code-layout
decision_tags:
  inline: stable-hot
  layout: risky-order
```

LLVM pass 可根据标签选择策略：

| 状态 | 编译器行为 |
|---|---|
| stable-hot | 正常激进优化 |
| stable-cold | 正常冷代码拆分 |
| risky-hot | 使用保守内联阈值 |
| risky-layout | 避免细粒度重排 |
| unknown | 回退静态启发式 |

这比给编译器一个看似精确但实际不可信的 count 更实用。

---

## 10. 主动稀疏校准

DRAPER 可以完全无插桩运行，也可以在允许的场景中加入极少量校准。

### 10.1 哪些位置需要校准

高风险位置包括：

1. 多个 inline context 汇聚到同一 callee entry；
2. 一个 IR block 展开成多个 binary blocks；
3. 多个 IR block 合并成一个 binary block；
4. hot/cold 边界；
5. branch probability 接近阈值；
6. sample coverage 极低但结构上可能是热块；
7. hash stale 但行表仍可匹配的位置。

### 10.2 校准收益分数

对候选实体 `e`：

$$
\begin{aligned}
\text{score}(e) &= p_{\text{ambiguity}}(e) \\
&\quad \cdot p_{\text{threshold\_crossing}}(e) \\
&\quad \cdot \text{impact}(e) \\
&\quad / \left(\text{runtime\_cost}(e) + \epsilon\right)
\end{aligned}
$$

其中 `impact(e)` 由静态结构估计：

1. 是否是函数 entry；
2. 是否是高分支扇出块；
3. 是否在 loop header/latch；
4. 是否是调用点；
5. 是否影响 code layout；
6. 是否接近 inline threshold。

### 10.3 校准机制

选择 top-K 实体插入：

1. pseudo-probe；
2. 单边计数器；
3. 复用已有 affine variable；
4. 轻量 entry counter。

硬预算：

$$
\begin{aligned}
\text{per function}        &\le K_f \text{ calibration anchors} \\
\text{per loop}            &\le K_l \text{ calibration anchors} \\
\text{dynamic instructions} &\le \text{runtime budget} \\
\text{profile size}        &\le \text{storage budget}
\end{aligned}
$$

这样形成 sample + sparse-anchor 混合模式，而不是回到全量 instrumentation。

---

## 11. 完整算法伪代码

```text
Algorithm DRAPER

Input:
    source S
    compiler flags F
    optimized binary B
    sample records R
    optional exact profiles L for training
    optional calibration budget K

Output:
    decision-safe IR profile P*
    uncertainty intervals U
    decision tags T
    audit report A

Phase 1: Evidence Construction
    G_IR   <- compile S with audit pipeline
    G_BIN  <- recover CFG from B
    TEG    <- build transformation evidence graph
    for each mapping edge:
        attach evidence type, pass, confidence, ambiguity

Phase 2: Runtime Evidence Aggregation
    O      <- aggregate samples onto TEG
    q      <- estimate sampling rates
    sigma  <- estimate observation noise
    a      <- compute mapping ambiguity

Phase 3: Flow-Consistent MAP
    x0     <- profi/MAP initialization
    repeat until convergence:
        update transform ratios lambda
        update z = A(lambda) x
        update graph prior mu, Sigma
        solve constrained MAP
        project x back to flow manifold

Phase 4: Decision Risk Analysis
    D      <- extract PGO predicates from LLVM
    U      <- estimate posterior intervals
    D_risk <- select predicates crossing thresholds
    estimate flip probability and cost for each d in D_risk

Phase 5: Risk Projection
    delta  <- solve bounded risk projection
    x*     <- project_to_flow_manifold(x + delta)
    T      <- emit stable / risky / unknown tags

Phase 6: Optional Sparse Calibration
    C      <- select high-score anchors under budget K
    if C is not empty:
        rebuild sparse anchors C
        rerun Phase 3 and Phase 5

Phase 7: Audit
    check CFG flow residual
    check call graph residual
    check interval coverage
    check decision stability
    emit P*, U, T, A
```

---

## 12. 多智能体工程化形式

如果论文希望包装成 agent-based 方法，可以用五个 typed agents。它们不是 prompt 驱动的自由推理，而是显式程序分析模块。

| Agent | 输入 | 输出 | 校验机制 |
|---|---|---|---|
| Evidence Agent | source, flags, binary, samples | TEG | evidence coverage + hash check |
| Solver Agent | TEG, counts, noise model | posterior profile | CFG/call invariants |
| Critic Agent | posterior profile, LLVM pipeline | predicates + flip probabilities | decision replay |
| Selector Agent | uncertainty, predicate risk | sparse calibration set | runtime budget |
| Auditor Agent | final profile, TEG, decisions | audit report | regression tests |

关系是流水线加反馈：

```text
Evidence -> Solver -> Critic
                        |
                        v
                  high-risk predicates
                        |
                        v
                  Selector -> Solver
                        |
                        v
                     Auditor
```

这个结构特别适合 FSE/ASE/ISSTA：每个 agent 都是可测试组件，论文可以强调机制可复现和失效可控。

---

## 13. 可靠性保证

### 13.1 结构可行性

只要静态图正确，输出满足：

1. CFG flow conservation；
2. call graph consistency；
3. loop constraints；
4. non-negativity。

这比纯神经网络 counts 预测更可靠。

### 13.2 决策稳定性

对单调谓词 `d`，如果后验置信区间不跨阈值，则输出决策是稳定的。

例如：

$$
P(\text{block is hot} \mid \text{sampled evidence}) \ge 1 - \alpha
$$

如果区间下界仍高于 hot threshold，则 hot 判定具有 `1-alpha` 稳定度。

### 13.3 有界修正

风险投影限制：

$$
\|\delta_x\|_1 \le \text{budget}
$$

因此 DRAPER 不会为了让某个局部决策变好看而无限制改写 profile。

### 13.4 可审计性

Auditor 输出：

```yaml
flow_residual:
  max_function: 0.00013
  p99_function: 0.00004
call_residual:
  max_context: 0.00021
coverage:
  pseudo_probe: 87.2%
  debug_info: 96.4%
  cfg_hash: 91.0%
  missing: 1.7%
risky_predicates:
  inline: 23
  layout: 41
  branch: 18
unknown_entities:
  count: 102
  examples: [...]
```

---

## 14. LLVM 原型实现

建议实现三层。

### 14.1 Evidence layer

新增工具或 pass：

```bash
opt -passes=draper-evidence \
    -draper-teg-output=teg.yaml
```

输入：

1. IR；
2. optimization remarks；
3. pseudo-probe metadata；
4. debug info；
5. FuncHash。

输出：

```yaml
functions:
  - id: main
    hash: 0x7f21...
    blocks:
      - ir_bb: entry
        binary_blocks: [0x4012f0]
        evidence: pseudo-probe
      - ir_bb: if.then
        binary_blocks: [0x401310, 0x401390]
        relation: clone
        pass: jump-threading
```

### 14.2 Solver layer

独立求解器：

```bash
draper-solve \
  --teg teg.yaml \
  --samples perf.data \
  --out draper.prof \
  --risk-report risk.yaml
```

实现顺序：

1. 用 LLVM profi 初始化；
2. 做 TEG-aware MAP；
3. 做 decision-risk projection；
4. 输出 LLVM sample profile。

### 14.3 Consumer layer

扩展 sample profile metadata：

```text
!draper.interval
!draper.ambiguity
!draper.decision_tag
```

第一版只接入：

1. sample profile loader；
2. BranchProbabilityInfo；
3. BlockFrequencyInfo；
4. inliner；
5. loop unroll/vectorization；
6. basic block layout。

---

## 15. 实验设计

### 15.1 Baseline

至少对比：

1. `-O3`，无 PGO；
2. exact instrumentation PGO，作为 upper bound；
3. AutoFDO / llvm-profgen raw sample profile；
4. LLVM profi；
5. LLVM CSSPGO / pseudo-probe；
6. ProfiX 或其复现版本。

如果做系统侧实验，再加入：

7. BOLT；
8. Propeller。

### 15.2 Profile 层指标

1. block count RMSE；
2. overlap；
3. edge probability error；
4. Top-K hot block F1；
5. Spearman/Kendall rank correlation；
6. missing count recovery；
7. CFG flow residual；
8. call graph residual。

### 15.3 Decision 层指标

这是 DRAPER 的核心评价贡献：

1. inline decision flip rate；
2. layout order disagreement；
3. branch probability threshold flip rate；
4. loop unroll/vectorization decision flip rate；
5. hot/cold split disagreement；
6. code size delta；
7. compile time delta。

### 15.4 End-to-end 指标

1. runtime speedup over `-O3`；
2. speedup over raw sample PGO；
3. speedup over profi；
4. speedup over CSSPGO；
5. I-cache miss；
6. branch miss；
7. profile size；
8. solve time；
9. calibration overhead。

### 15.5 消融

必须做：

| 配置 | 目的 |
|---|---|
| DRAPER without TEG | 验证变换证据图价值 |
| DRAPER without uncertainty | 验证后验区间价值 |
| DRAPER without decision projection | 验证决策风险目标价值 |
| DRAPER without graph prior network | 验证学习先验价值 |
| DRAPER without active calibration | 验证稀疏校准价值 |
| L1 loss only | 与传统 profile 还原对比 |
| decision loss only | 证明必须保留流约束 |

### 15.6 鲁棒性

1. 降低采样率：1/16、1/64、1/256；
2. 注入 PMU skid；
3. 删除 debug info；
4. 删除 pseudo-probe；
5. 模拟 hash stale；
6. 跨编译器版本；
7. 跨 x86-64 / AArch64；
8. leave-one-benchmark-out 训练；
9. workload shift。

---

## 16. 开源 Benchmark 数据集

当前没有一个现成公开数据集同时包含：

1. source；
2. exact IR profile；
3. optimized binary；
4. realw；
5. transformation evidence。

因此论文应构造并开源一个 benchmark corpus，这本身是 artifact contribution。

### 16.1 训练和调试

| 数据集 | 作用 |
|---|---|
| LLVM test-suite / SingleSource / MultiSource | 编译快，适合构造 exact + sampled pairs |
| AnghaBench | 大规模可编译 C 函数，训练图先验和泛化 |
| PolyBench/C | 循环密集内核，测试 trip count 和 loop PGO |
| Rodinia | CPU 部分 HPC workload，测试多线程采样偏差 |
| PARSEC | 并行 C/C++ 应用 |

### 16.2 标准评测

| 数据集 | 作用 |
|---|---|
| SPEC CPU 2017 C/C++ | PGO 标准评测 |
| SPEC CPU 2006 C/C++ | 补充比较 |
| Embench | 嵌入式/终端风格 |
| MiBench | 轻量终端 workload |
| MediaBench | 多媒体内核 |

SPEC 不是开源 license，但学术上标准且可获取。论文应将开源 corpus 与 SPEC 分开报告。

### 16.3 真实应用

| 程序 | 领域 | 测试目标 |
|---|---|---|
| SQLite | 数据库 | 调用密集、内联复杂 |
| Redis | 服务端 | 高频热路径、branch/layout |
| nginx | 网络 | 热冷路径复杂 |
| OpenSSL | 密码协议 | 热点稳定、跨平台 |
| zlib / zstd / xz | 压缩 | 循环密集 |
| FFmpeg | 多媒体 | AArch64 终端代表 |
| libjpeg-turbo / libwebp | 图像 | 移动终端真实负载 |
| OpenCV | 视觉 | 大型 C++、模板和内联复杂 |
| LLVM/Clang | 编译器自身 | 大 CFG、复杂优化管线 |
| PostgreSQL / MySQL / ClickHouse | 数据库 | 大规模系统级评测 |

### 16.4 AArch64 / 鸿蒙补充

建议增加：

1. FFmpeg AArch64；
2. libjpeg-turbo / libwebp AArch64；
3. TensorFlow Lite XNNPACK delegate；
4. llama.cpp CPU backend；
5. OpenCV mobile modules。

这些主要用于证明终端 C/C++ 场景可部署。

---

## 17. 数据集构建协议

对每个 benchmark program：

```text
Build A: exact IR profile
    clang -O3 -fprofile-instr-generate -fcoverage-mapping
    run representative workload
    llvm-profdata merge
    save exact IR profile

Build B: sampled optimized binary
    clang -O3
    run same workload under perf/LBR or ARM SPE/ETM
    save optimized binary and raw sample records

Build C: audit-only binary
    clang -O3 with pseudo-probe metadata but no runtime counters
    save pseudo-probe/hash evidence

Alignment:
    match source files, functions, IR CFG, binary CFG
    record pass remarks and transformation evidence

Split:
    train:
        LLVM test-suite + AnghaBench + PolyBench
    validation:
        one real application family
    test:
        SPEC + large real apps, leave-program-out
```

采样来源分两档：

1. **真实硬件采样**
   - x86-64：PEBS + LBR；
   - AArch64：SPE / BRBE / ETM。

2. **可控仿真采样**
   - 从 exact profile 按已知采样率降采样；
   - 注入已知 skid；
   - 随机遮蔽 branch edge；
   - 模拟 clone split ratio。

真实硬件保证外部效度，仿真采样保证统计强度和可控性。

---

## 18. 论文贡献陈述

可以在论文中写四点：

1. We identify that sampled-profile restoration should be evaluated by PGO decision stability, not only raw count accuracy.

2. We propose DRAPER, a transformation-evidence graph that explicitly models inlining, cloning, splitting, merging, loop transformation, and missing mappings between optimized binaries and early IR.

3. We design a flow-consistent Bayesian inference algorithm whose neural component only predicts priors and uncertainty, while the final profile is projected onto a feasible control-flow manifold.

4. We introduce a decision-risk projector and sparse calibration mechanism that minimizes expected flips of high-cost PGO predicates under a runtime budget.

一句话摘要：

> DRAPER restores profiles not by making every count look exact, but by making the compiler decisions that consume those counts safe under sampled uncertainty.

---

## 19. 主要风险与降级策略

| 风险 | 处理策略 |
|---|---|
| LLVM pass 语义复杂，谓词抽取困难 | 第一版只接入 5 个高收益 pass |
| transformation remarks 不完整 | 与 pseudo-probe/CFG hash 融合，缺失时退回 profi |
| exact workload 与 sampled workload 不一致 | 使用固定 workload，并报告 workload-shift 实验 |
| AArch64 PMU 能力差异 | 分层报告 x86-LBR、ARM ETM、ARM SPE |
| ML prior 泛化不足 | 无先验模式仍保留 TEG + flow + risk projection |
| risk projection 过拟合 benchmark | leave-one-program-out 和阈值敏感性分析 |
| runtime 提升不稳定 | 报告几何均值、分布、失败 case 和决策翻转原因 |

---

## 20. 最小可行论文版本

如果资源有限，先做最小可行版本：

1. 只支持 LLVM；
2. 只支持 C/C++；
3. 只使用 pseudo-probe、FuncHash、debug info、optimization remarks；
4. 只做 CFG + call graph 流守恒；
5. 只接入 inliner、branch probability、BlockFrequencyInfo、layout 四个消费点；
6. 输出 count + interval + risk tag；
7. 在 LLVM test-suite、PolyBench、SPEC CPU 2017、SQLite、zstd、FFmpeg 上验证。

这个版本已经足够形成 CCF-A 级 novelty：

```text
TEG
+ flow-consistent posterior
+ PGO decision risk projection
+ sparse calibration
```

其中任意一个单独都不够新，但组合起来的核心 insight 非常清晰：

> 采样 profile 还原的终点不是 counts，而是编译器决策的安全执行。
