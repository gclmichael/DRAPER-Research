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

$\rho$ 是 Huber 损失；$\mu_\theta$ 和 $\Sigma_\theta$ 是图先验网络预测的均值和方差；$\Omega(\lambda)$ 惩罚过碎或无证据的变换比例。求解使用块坐标下降。

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

其中 $\pi_{ij}$ 是静态证据给出的比例先验。若没有证据，则 $\pi_{ij}$ 为均匀分布。

#### Step 3.1 更新变换比例

固定 $x_t$，对每组可分离变换边求解：

$$
\lambda_{t+1} \leftarrow \operatorname{argmin}_{\lambda}\ J(\lambda;\, x_t)
$$

具体策略：

1. 一个 IR 块分裂成两个二进制块时，按两个局部样本桶更新 split ratio；
2. 多个 clone 共享同一 IR 块时，按样本量和证据置信度更新 clone weight；
3. 内联上下文比例用 caller edge 的局部观测更新 callee entry 分配；
4. 更新后把比例截断到 $[\epsilon, 1-\epsilon]$，避免零比例永久关闭合法路径。

当变换边局部目标为二次可微时，用投影牛顿步：

$$
\lambda \leftarrow \Pi_{\Lambda}\!\left(\lambda - H_{\lambda}^{-1}\,\operatorname{grad}_{\lambda} J\right)
$$

否则用坐标搜索。$\Pi_\Lambda$ 表示单纯形或箱式约束投影。

#### Step 3.2 更新 IR 频次

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

$\text{margin}_d(x) \ge 0$ 表示谓词处于错误侧或阈值安全裕量之内。若无法把谓词推到安全侧，DRAPER 不硬翻转，而是降低该谓词置信度并触发保守策略。

为了降低非凸性，对线性谓词可使用连续 hinge：

$$
\phi_d(x) = \max\!\left(0,\ \text{margin}_d(x) + \tau_{\text{safe}}\right)^2
$$

其中 $\tau_{\text{safe}}$ 是阈值安全裕量。修正子问题可在 $D_{\text{risk}}$ 中按连通子图分解；只有共享 TEG 邻居或 CFG 约束的谓词才需要联合求解。

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