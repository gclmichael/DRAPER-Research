# DRAPER: Decision-Risk-Aware Profile Evidence Restoration

**DRAPER（决策风险感知的采样 Profile 还原算法）** —— 面向采样式反馈编译优化（Sampling-based PGO）的 Profile 证据恢复与编译决策保护研究实现。

采样式反馈优化直接对优化后的二进制进行稀疏硬件采样，存在**优化前后程序结构不一致（内联、克隆、合并、循环展开导致映射歧义）**以及**计数噪声在编译器阈值附近触发错误决策翻转（“计数更准但性能变慢”）**两大痛点。本仓库在控制流守恒与模块内调用一致硬约束下，构建编译变换证据图（TEG）反演早期 IR 频次与不确定性区间，并通过少量稀疏主动校准与有界决策风险投影保护关键编译决策。

---

## 一、 与老师方案（《DRAPER-CCF-A 算法模型方案》）的逐项实现核对（如实描述）

| 方案模块（对应方案章节） | 当前实现状态 | 具体代码位置与如实说明 |
| :--- | :--- | :--- |
| **1. 编译变换证据图 TEG**（§3.1, §4） | ✅ 已实现四类变换接入观测矩阵 | [`draper/evidence.py`](draper/evidence.py)、[`draper/transformed.py`](draper/transformed.py)、[`collect_transformed_observations.py`](collect_transformed_observations.py)：利用 `@llvm.pseudoprobe` 建立优化前后来源映射，将**内联（inline）、克隆（clone）、二路互斥分支合并（merge-exclusive）、循环展开（loop）**构建为稀疏观测算子行系数，支持多映射候选似然加权。当前副本比例由独立校准运行提供，在离散候选集合上加权，尚未做连续比例与频次的交替优化。 |
| **2. 流一致反演与不确定性区间**（§3.3, §8） | ✅ 已实现硬约束 LP + 重采样区间 + 不可辨识上下界 | [`draper/model.py`](draper/model.py)、[`draper/transformed.py`](draper/transformed.py)：严格满足早期 IR 控制流守恒（流入=流出）、模块内封闭调用一致与非负性；通过消去零观测与零初值辅助变量加速大规模图 LP 求解；结合观测重采样、映射候选重采样与不可辨识范围（`ambiguity_bounds`）输出 95% 置信区间（在 `aha` 上 100 次独立评价逐块覆盖率达 **94.90%**）。这是经验检验的近似区间，不等同于精确贝叶斯后验。 |
| **3. 稀疏主动校准**（§10） | ✅ 已实现按动态更新预算选点与实测重算 | [`draper/decision_risk.py`](draper/decision_risk.py) (`budgeted_anchors`)、[`draper/model.py`](draper/model.py) (`select_anchors`)：按“预期风险收益 / 预计动态更新次数”排序选点，限制每程序 $\le 3$ 点、每函数 $\le 2$ 点、每循环 $\le 1$ 点、预计动态更新次数 $\le 1\%$，实际编译插桩测量后作为硬等式约束重解。这是贪心启发式选点，尚未过滤 `unreachable` 块（已在 SQLite 诊断中记录）。 |
| **4. 真实编译决策抽取**（§3.4, §8, §20） | ✅ 已接入 4 类核心消费点 | [`draper/llvm_bridge.cpp`](draper/llvm_bridge.cpp)、[`draper/decision_risk.py`](draper/decision_risk.py)、[`run_core_risk.py`](run_core_risk.py)：抽取 ① 分支概率 $>80\%$（`isEdgeHot`）、② 基本块相对函数入口热度 $>10\times$、③ 调用点内联成本裕量（`getInlineCost` + Remarks）、④ 机器基本块布局次序（`llc -stop-after=block-placement`）。 |
| **5. 决策风险有界投影与回放验收**（§3.5, §9） | ✅ 已实现 LP 裕量投影 + 真实编译回放 | [`draper/decision_risk.py`](draper/decision_risk.py) (`bounded_projection`, `replay_acceptance`)：在总计数 L1 绝对改变量 $\le 1\%$ 内最小化代价加权的决策裕量损失，冻结无关连通分量；重新编译回放候选 Profile，要求所有后验一致的稳定决策不翻转且实际预期风险下降，否则回退至校准解。当前决策错误代价 $w$ 使用“频次 $\times$ 静态指令数”近似，尚未通过单决策干预实验学习真实耗时代价。 |
| **6. 图神经网络先验（GNN Prior）**（§6, §19） | ⏸ 按方案 §19/§20 最小可行版本暂缓 | 遵循方案第 20 节最小可行版本（MVV）设计，当前采用无学习先验模式（以流守恒与弱正则为中心），验证 TEG + 流一致推断 + 稀疏校准 + 决策风险投影的核心闭环。 |

---

## 二、 公开基准套件完整实验结果汇总（2026-11-11 全部完成）

*   **运行环境**：`Intel(R) Xeon(R) Platinum 8358P CPU @ 2.60GHz`（计时绑定单核 CPU 127），`Clang / LLVM 23.1.4`。
*   **计时方法**：每个程序各编译版本交错运行（Paired Interleaved Rounds）**15 轮**，每轮目标批次时长 `0.3s`，Bootstrap（2000 次抽样）计算配对几何平均加速比与 95% 置信区间。输出验证在计时外独立执行。
*   **完整逐程序数据入口**：
    *   完整 Markdown 报告：[`results/PUBLIC_RESULTS.md`](results/PUBLIC_RESULTS.md)（同步保存于 [`data/public-results-v1/results.md`](data/public-results-v1/results.md)）
    *   完整 JSON 汇总数据：[`results/public-suites-summary.json`](results/public-suites-summary.json)

### 1. 五大套件总体对照（相对 LLVM `profi` 基线）

| 套件与观测模式 | 已列入 / 完成算法 / 有效计时 | 相对 `profi` 几何平均加速 | 风险投影相对校准额外加速 | 显著变慢程序数 | “计数改善却显著变慢”反例数（校准前 $\rightarrow$ 投影后） | 其中下降超 1% 的反例数 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PolyBench（TEG 优化后观测）** | **27 / 27 / 27** | **+0.325%** | **+0.151%** | **0** | **1 $\rightarrow$ 0** | **0** |
| **Rodinia / MediaBench（TEG 优化后观测）** | **10 / 10 / 10** | -0.223% | -0.558% | **0** | **0 $\rightarrow$ 0** | **0** |
| **LLVM 多源程序（早期计数对照）** | **40 / 40 / 40** | -0.642% | -0.074% | 10 | 8 $\rightarrow$ 6 | 4 |
| **LLVM 多源程序（TEG 优化后观测）** | **40 / 40 / 40** | -0.643% | **+0.071%** | 10 | **2 $\rightarrow$ 2**（较早期对照减少 67%） | **1**（较早期对照减少 75%） |
| **现代 SQLite、zstd、FFmpeg（TEG 优化后观测）** | **3 / 3 / 3** | -2.052%（`calibrated` 为 -0.56%） | -1.497% | 2 | 0 $\rightarrow$ 1 | 1 |

### 2. 现代大型工业应用详细数据（独立测试输入评价）

详细计时与审计文件见 [`results/sqlite-modern-timing.json`](results/sqlite-modern-timing.json)、[`results/zstd-timing.json`](results/zstd-timing.json)、[`results/ffmpeg-timing.json`](results/ffmpeg-timing.json) 及对应的 `*-audit.json`。

| 程序（集合 / 源码行数） | 基本块数 | 块计数相对误差（`profi` $\rightarrow$ `DRAPER`） | 区间覆盖率（全部块 / 实际执行块） | 相比无 PGO `-O3` (`baseline`) 提速 | 相比 `profi` 速度差异（`calibrated` 阶段） | 相比 `profi` 速度差异（`risk-projected` 阶段） |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **ffmpeg**（测试集 / 136,356 行） | 78,431 | **1.65% $\rightarrow$ 0.18%** | 96.31% / 80.37% | `profi`: +18.93%<br>`calibrated`: **+18.15%** `[+16.65%, +19.31%]`<br>`risk-projected`: **+14.62%** | **-0.66%** `[-1.25%, +0.30%]`（统计持平） | -3.63% `[-4.26%, -2.61%]` |
| **sqlite-modern**（开发集 / 311,070 行） | 87,927 | 3.21% $\rightarrow$ 10.85% | 95.10% / 68.69% | `profi`: +12.08%<br>`calibrated`: **+11.28%** `[+9.87%, +13.10%]`<br>`risk-projected`: **+9.63%** | **-0.72%** `[-1.71%, +0.30%]`（统计持平） | -2.19% `[-3.78%, -1.00%]` |
| **zstd**（测试集 / 43,441 行） | 26,818 | **69.95% $\rightarrow$ 52.67%** | 96.60% / 74.25% | `profi`: +1.83%<br>`calibrated`: **+1.51%** `[-1.29%, +6.94%]`<br>`risk-projected`: **+1.51%** | **-0.31%** `[-1.42%, +0.41%]`（统计持平） | **-0.31%**（回放未接受投影，保持 `calibrated`） |

---

## 三、 仓库目录与文件结构说明

### 1. 核心算法包（`draper/`）
| 文件路径 | 功能说明 |
| :--- | :--- |
| [`draper/model.py`](draper/model.py) | CFG 流守恒与调用图约束系统（`FlowSystem`）、加权 L1/Huber 稀疏 LP 求解器（`fit_linear`/`fit`）、重采样区间与分支风险分析（`uncertainty`/`risks`）、锚点约束投影（`anchored_profi`）。 |
| [`draper/decision_risk.py`](draper/decision_risk.py) | 四类编译决策局部线性化（`linear_decisions`）、硬预算有界风险投影（`bounded_projection`）、真实编译回放验收（`replay_acceptance`）、动态更新预算选点（`budgeted_anchors`）。 |
| [`draper/transformed.py`](draper/transformed.py) | 编译变换证据图（TEG）系数生成（`relation`/`exclusive_merges`）、离散映射候选泊松似然加权推断（`infer`）、不可辨识上下界传播（`propagate_bounds`/`ambiguity_bounds`）、区间校准与评估（`intervals`/`coverage`/`calibrate_width`）。 |
| [`draper/evidence.py`](draper/evidence.py) | 解析 LLVM IR 中的 `@llvm.pseudoprobe`，提取优化前后的基本块映射与跨函数内联/多副本克隆证据。 |
| [`draper/llvm_bridge.cpp`](draper/llvm_bridge.cpp) | LLVM C++ 插件桥接器：拆分关键边、导出 CFG/调用图/循环头、插入全量或稀疏计数器、调用内置 `profi`、写回 `branch_weights` 元数据、导出真实 BPI/BFI/InlineCost 决策。 |
| [`draper/sample_profile.py`](draper/sample_profile.py) | 解析真实硬件采样（`perf script` / `llvm-profgen`）生成的采样 Profile 并对齐至 CFG。 |
| [`draper/workload.py`](draper/workload.py) | 多源文件工作负载构建、执行与输出哈希/退出码正确性校验。 |

### 2. 实验驱动与评测脚本（根目录）
| 文件路径 | 功能说明 |
| :--- | :--- |
| [`run_core_risk.py`](run_core_risk.py) | 完整 DRAPER 流程主驱动：流一致恢复、稀疏主动校准、不确定性重采样、决策风险投影与编译器决策回放。 |
| [`collect_transformed_observations.py`](collect_transformed_observations.py) | 优化后探针观测采集驱动：插入伪探针、执行 `-O3` 优化、对优化后保留探针插桩以测量副本比例与优化后采样观测。 |
| [`measure_runtime.py`](measure_runtime.py) | 单核绑定、多版本随机交错运行（Paired Interleaved Rounds）计时器与 Bootstrap 置信区间计算。 |
| [`audit_core_results.py`](audit_core_results.py) | 最终输出独立审计：验证非负性、流守恒残差、修改预算上限、锚点零误差及“计数改善却性能变慢”反例统计。 |
| [`summarize_public_results.py`](summarize_public_results.py) | 汇总全部公开套件（LLVM MultiSource、PolyBench、现代应用、Rodinia/MediaBench）的完成状态、加速比与区间覆盖率，生成 [`results/PUBLIC_RESULTS.md`](results/PUBLIC_RESULTS.md)。 |
| [`run_all_core.py`](run_all_core.py) / [`run_teacher_apps.py`](run_teacher_apps.py) | 批量运行多源套件与现代大型应用（SQLite/zstd/FFmpeg）的采集、算法恢复、计时与审计流水线。 |
| [`evaluate_compiled_intervals.py`](evaluate_compiled_intervals.py) / [`evaluate_transformed.py`](evaluate_transformed.py) | 100 次独立抽样的区间覆盖率校准与变换观测恢复评估实验。 |
| [`run_draper.py`](run_draper.py) / [`run_real_pgo.py`](run_real_pgo.py) | 早期 Embench 6 项原型驱动与真实硬件 LBR 采样 PGO 对比驱动。 |

### 3. 实验结果与审计数据（`results/` 与根目录 JSON）
| 文件路径 | 内容说明 |
| :--- | :--- |
| [`results/PUBLIC_RESULTS.md`](results/PUBLIC_RESULTS.md) | 全部 80 项公开套件实验的完整结果表格（含每项加速比、95% CI、反例统计与逐程序区间覆盖率）。 |
| [`results/public-suites-summary.json`](results/public-suites-summary.json) | 全部公开套件的结构化汇总 JSON 数据。 |
| [`results/public-corpus-index.json`](results/public-corpus-index.json) | 70 个独立工作负载的源码规模、应用家族及 `development`/`validation`/`test` 划分索引。 |
| [`results/sqlite-modern-timing.json`](results/sqlite-modern-timing.json) / [`results/zstd-timing.json`](results/zstd-timing.json) / [`results/ffmpeg-timing.json`](results/ffmpeg-timing.json) | 三个现代大型应用 15 轮交错计时的逐轮原始耗时与配对加速比。 |
| [`results/sqlite-modern-audit.json`](results/sqlite-modern-audit.json) / [`results/zstd-audit.json`](results/zstd-audit.json) / [`results/ffmpeg-audit.json`](results/ffmpeg-audit.json) | 三个现代大型应用的流守恒残差、计数相对误差、修改量与区间覆盖率审计报告。 |
| [`core-risk-v4-summary.json`](core-risk-v4-summary.json) / [`large-core-v2-summary.json`](large-core-v2-summary.json) | Embench 6 项原型与 `spiff` 早期验证的完整汇总、计时与审计 JSON。 |

### 4. 研究文档与阶段报告
| 文件路径 | 内容说明 |
| :--- | :--- |
| [`组会汇报摘要_2026-11-11.md`](组会汇报摘要_2026-11-11.md) | 最新组会汇报文档：问题逻辑链、方案对齐表、完整套件实验结果与现存问题诊断。 |
| [`OBSERVATION_MODEL_2026-11-10.md`](OBSERVATION_MODEL_2026-11-10.md) | 变换观测方程、映射歧义建模、100 次独立抽样区间覆盖率实验与 SQLite 区间诊断详述。 |
| [`CORE_IMPLEMENTATION_2026-11-10.md`](CORE_IMPLEMENTATION_2026-11-10.md) | 决策风险最小实现细节、四类决策范围、早中期实验结果与反例原因剖析。 |
| [`LARGE_DATASET_2026-11-10.md`](LARGE_DATASET_2026-11-10.md) | 70 项公开基准数据集构建、多输入校验与开发/验证/测试集划分说明。 |
| [`TEACHER_IDEA_AUDIT_2026-11-09.md`](TEACHER_IDEA_AUDIT_2026-11-09.md) | 早期原型与老师方案的逐项差距审计记录。 |
| [`DRAPER.md`](DRAPER.md) / [`REAL_HARDWARE_PGO.md`](REAL_HARDWARE_PGO.md) | 基础流约束模型说明与真实硬件 LBR 采样管线验证记录。 |

---

## 四、 单元测试与复现命令

### 1. 运行全部单元测试（共 14 个测试文件）
```bash
python3 -m unittest discover -s tests -v
```

### 2. 复现单程序优化后观测与决策风险恢复流程
```bash
# 1) 采集优化后伪探针观测与副本比例
python3 collect_transformed_observations.py data/all-flows-v3/aha \
  --output data/my-aha-obs --bridge data/probe-tools-v1/draper-llvm

# 2) 运行流一致恢复、稀疏主动校准、20次重采样与决策风险回放投影
python3 run_core_risk.py data/my-aha-obs/experiment \
  --output data/my-aha-core --bridge data/probe-tools-v1/draper-llvm \
  --fresh-calibration --budget 0.01

# 3) 单核绑定 15 轮交错计时
python3 measure_runtime.py data/my-aha-core \
  --variants calibrated risk-projected --rounds 15 --seconds 0.3

# 4) 独立审计流守恒残差、修改预算与计数误差
python3 audit_core_results.py data/my-aha-obs/experiment data/my-aha-core
```
