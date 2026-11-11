#!/usr/bin/env python3
# Lead's conversion of blocks B023-B066 (formal core, no CJK inside these blocks).
# Every original identifier is preserved; only typesetting changes.
import json

O = {}

O["B023"] = r"$$I = (S,\ F,\ B,\ R)$$"

O["B024"] = r"$$O = (x^*,\ U,\ T,\ A)$$"

O["B025"] = r"$$H_t = (G,\ E,\ w,\ x_t,\ \lambda_t,\ z_t,\ \Sigma_t,\ D_t)$$"

O["B026"] = r"""$$
\begin{aligned}
\text{given observations}\quad & r \in \mathbb{R}_{+}^{|V_{\text{BIN}}|} \\
\text{find}\quad & x \in \mathbb{R}_{+}^{|V_{\text{IR}}|} \\
\text{such that}\quad & x \text{ is flow-consistent on IR} \\
\text{and}\quad & r \text{ is statistically explained by } T(x) \\
\text{and}\quad & \text{PGO predicates consumed from } x \text{ are decision-stable}
\end{aligned}
$$"""

O["B027"] = r"$$z = T(\lambda)\, x$$"

O["B028"] = r"""$$
\begin{aligned}
X = \{\, x \mid\ & x \ge 0, \\
& C_{\text{CFG}}\,x = 0, \\
& C_{\text{call}}\,x = 0, \\
& C_{\text{loop}}\,x \le 0 \,\}
\end{aligned}
$$"""

O["B029"] = r"""$$
\min_{x \in X,\ \lambda \in \Lambda}\ \
  L_{\text{sample}}\big(T(\lambda)x,\ r\big)
  + L_{\text{prior}}(x)
  + L_{\text{transform}}(\lambda)
$$"""

O["B030"] = r"""- source $S$, flags $F$, optimized binary $B$
- optimization remarks $Y$
- pseudo-probe metadata $M$
- debug info $L$
- FuncHash $K$"""

O["B031"] = r"$$\text{TEG}\ \ E = (V_{\text{IR}},\ V_{\text{BIN}},\ V_{\text{CTX}},\ E_T)$$"

O["B033"] = r"""- TEG $E$
- sample records $R$
- PMU event type
- branch/call stack availability"""

O["B034"] = r"$$\Sigma_0 = \operatorname{diag}(\alpha_s + s_i + \text{coverage\_penalty}_i)$$"

O["B035"] = r"""$$x_0 \leftarrow \operatorname{argmin}_{x \in X}\ \|x - \text{profi}(r, G)\|_1$$"""

O["B036"] = r"""$$x_0 \leftarrow \operatorname{argmin}_{x \in X}\ \mathrm{KL}(x \,\|\, \text{uniformPrior})$$"""

O["B037"] = r"initial state $(E,\ w,\ x_0,\ \lambda_0,\ z_0,\ \Sigma_0)$"

O["B038"] = r"""- state after Phase 2
- observations $s$
- sampling rates $q$
- noise scales $\sigma$"""

O["B039"] = r"""$$
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
$$"""

O["B040"] = r"""$$L_{\text{sample}}(z, r) = \sum_i \left[\, z_i - r_i \log z_i
  + \alpha_i \log\!\left(1 + \frac{z_i}{\alpha_i}\right) \right]$$"""

O["B041"] = r"""$$L_{\text{sample}}(z, r) = \sum_i \rho\!\left(\frac{q_i z_i - r_i}{\sigma_i}\right)$$"""

O["B042"] = r"""$$\Omega(\lambda) = \sum_{(i,j)} \eta_{ij}\,\mathrm{KL}(\lambda_i \,\|\, \pi_{ij})$$"""

O["B043"] = r"""$$\lambda_{t+1} \leftarrow \operatorname{argmin}_{\lambda}\ J(\lambda;\, x_t)$$"""

O["B044"] = r"""$$\lambda \leftarrow \Pi_{\Lambda}\!\left(\lambda - H_{\lambda}^{-1}\,\operatorname{grad}_{\lambda} J\right)$$"""

O["B045"] = r"""$$
\begin{aligned}
\min_x\ & \sum_i \rho\!\left(\frac{s_i - q_i z_i}{\sigma_i}\right)
   + 0.5\,(x - \mu_\theta)^\top \Sigma_\theta^{-1} (x - \mu_\theta) \\\\
\text{subject to:}\quad
  & C_{\text{CFG}}\,x = 0 \\
  & C_{\text{call}}\,x = 0 \\
  & C_{\text{loop}}\,x \le 0 \\
  & x \ge 0
\end{aligned}
$$"""

O["B046"] = r"$$x_{t+1} \leftarrow \text{ProjectToFlowManifold}(x_{t+1})$$"

O["B047"] = r"""$$\text{ProjectToFlowManifold}(y) = \operatorname{argmin}_{x \in X}\ \|x - y\|_D$$"""

O["B048"] = r"$$D_{ii} = \frac{1}{1 + r_i + \text{coverage}_i}$$"

O["B049"] = r"$$\Sigma_{t+1} = H^{-1} + \Sigma_{\text{prior}} + \Sigma_{\text{mapping}}$$"

O["B050"] = r"""$$\Sigma_{ii} = \frac{\sigma_i^2}{q_i^2 + \delta}
  + \Sigma_{\text{prior}\_ii}
  + \text{ambiguity}_i \cdot \sigma_{\text{map}}^2$$"""

O["B051"] = r"""$$
\begin{aligned}
\frac{|J_t - J_{t+1}|}{\max(1,\ |J_t|)} &< \eta_{\text{obj}} \\
\frac{\|x_{t+1} - x_t\|_1}{\max(1,\ \|x_t\|_1)} &< \eta_x \\
\max\ \text{residual}(C_{\text{CFG}}\,x,\ C_{\text{call}}\,x) &< \eta_{\text{flow}}
\end{aligned}
$$"""

O["B052"] = r"$$(x_{\text{MAP}},\ \lambda_{\text{MAP}},\ \Sigma_{\text{MAP}})$$"

O["B053"] = r"""- IR module
- target LLVM pipeline $P$
- MAP posterior $(x_{\text{MAP}},\ \Sigma_{\text{MAP}})$"""

O["B054"] = r"""$$d = \big(\ \text{predicate function } f_d,\ \
  \text{threshold } \theta_d,\ \
  \text{direction } \text{dir}_d,\ \
  \text{conservative fallback},\ \
  \text{aggressive action}\ \big)$$"""

O["B055"] = r"""$$
\begin{aligned}
y_d &= f_d(x_{\text{MAP}}) \\
\operatorname{var}(y_d) &= \operatorname{grad} f_d^\top\ \Sigma_{\text{MAP}}\ \operatorname{grad} f_d
\end{aligned}
$$"""

O["B056"] = r"$$\operatorname{var}(y_d) = g_d^\top\, \Sigma_{\text{MAP}}\, g_d$$"

O["B057"] = r"""$$p_{\text{flip}}(d) = \begin{cases}
  P(y_d \le \theta_d) & \text{if } \text{dir}_d = \text{greater} \\
  P(y_d \ge \theta_d) & \text{if } \text{dir}_d = \text{less}
\end{cases}$$"""

O["B058"] = r"$$\text{risk}(d) = c_d \, p_{\text{flip}}(d)$$"

O["B059"] = r"""$$\text{margin}_d(x) = \begin{cases}
  \theta_d - f_d(x) & \text{if } \text{dir}_d = \text{greater} \\
  f_d(x) - \theta_d & \text{if } \text{dir}_d = \text{less}
\end{cases}$$"""

O["B060"] = r"""$$
\begin{aligned}
\text{stable}(d) &\iff \text{margin}_d(x_{\text{MAP}}) > k \sqrt{\operatorname{var}(y_d)} \\
\text{risky}(d) &\iff \text{otherwise}
\end{aligned}
$$"""

O["B061"] = r"""$$D_{\text{risk}} = \{\, d \mid p_{\text{flip}}(d) \ge \tau_p \ \text{ and } \ c_d \ge \tau_c \,\}$$"""

O["B062"] = r"""- $x_{\text{MAP}}$
- $\Sigma_{\text{MAP}}$
- $D_{\text{risk}}$
- correction budget $\epsilon$"""

O["B063"] = r"""$$
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
$$"""

O["B064"] = r"$$\phi_d(x) = \max\!\left(0,\ \text{margin}_d(x) + \tau_{\text{safe}}\right)^2$$"

O["B065"] = r"""$$
\begin{aligned}
x^* &= x_{\text{MAP}} + \delta_x^* \\
U &= \text{intervals derived from } \Sigma(x^*) \\
T &= \text{decision stability tags} \\
A &= \text{audit report}
\end{aligned}
$$"""

# B032 (template pseudocode) and B066 (Algorithm 1 box) stay as fenced text blocks.

json.dump(O, open("out_lead.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("wrote out_lead.json with %d blocks" % len(O))
