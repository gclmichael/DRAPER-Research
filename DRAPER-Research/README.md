# DRAPER

**Decision-Risk-Aware Profile Evidence Restoration** — 采样式反馈优化中 profile 计数还原的研究原型。

采样式反馈优化只观测到一部分执行计数，且带噪。本原型在控制流与可见调用约束下恢复计数，
再用编译器决策回放检查受限的风险修正。当前已接入优化后伪探针计数、
内联与克隆副本比例、互斥合并候选和循环副本比例，并联合评价映射歧义与计数区间。

---

## 状态

原型阶段。2026-11-10 已加入决策风险修正器和较大的多源文件语料：

- LLVM CFG 导出与精确边计数
- 稀疏插桩（`--anchors`）与实际测量
- 流约束恢复、分支风险回退
- vs. LLVM `profi` 的对照基线
- 实际内联记录、存续分支及可唯一对应机器块排列的编译回放
- 成本加权决策损失、1% 总绝对修改量上限、稳定决策保护
- 按预计执行成本选点，并实际执行稀疏插桩
- 38 个应用家族、40 个工作负载的标准套件语料

原 40 项语料已全部完成全块采集、早期计数对照的恢复和有效计时。
优化后观测方法另行运行；PolyBench 和现代 SQLite、zstd、FFmpeg 正在扩展实验中。
SPEC CPU 因暂无授权材料按用户安排暂缓。
最新逐项状态见 [公开套件结果](data/public-results-v1/results.md)，
观测方程、区间覆盖率与当前限制见 [观测模型记录](OBSERVATION_MODEL_2026-11-10.md)，
源码规模和划分见 [主数据集说明](LARGE_DATASET_2026-11-10.md)。

---

## 快速开始

### 依赖

- LLVM 23 开发工具链（`llvm-config`、`opt`、`llc`、`clang`）
- Python 3 + NumPy + SciPy
- PyYAML（编译优化记录分析）

### 跑测试

```bash
python3 -m unittest discover -s tests -v
```

32 个测试，覆盖编译器桥接、流约束求解、风险预算、稳定决策保护、计数对应、计时配对和插桩选点。

### 跑一次实验

```bash
python3 run_draper.py \
  --programs crc32 slre qrduino nettle-aes picojpeg wikisort \
  --rate 0.015625 --missing 0.15 --bootstrap 20 --anchors 3 \
  --seed 42 --output data/my-draper-experiment

python3 analyze_remarks.py data/my-draper-experiment

python3 run_core_risk.py data/my-draper-experiment \
  --fresh-calibration --budget 0.01 --output data/my-core-experiment
python3 measure_runtime.py data/my-core-experiment \
  --variants calibrated risk-projected --rounds 15 --seconds 0.3
```

每次需指定新的输出目录，脚本拒绝覆盖已有实验。编译与执行顺序进行，
数学库单线程，每个外部命令限时 90 秒。

---

## 代码结构

| 路径 | 作用 |
|---|---|
| `draper/model.py` | 流约束恢复与决策风险模型 |
| `draper/decision_risk.py` | 成本加权投影、硬性修改预算、编译回放验收与预算选点 |
| `draper/evidence.py` | LLVM 伪探针变换来源图 |
| `run_core_risk.py` | 决策风险最小实现的实验驱动 |
| `build_large_corpus.py` / `index_large_corpus.py` / `validate_large_corpus.py` | 多源文件主语料的构建、计数对应及检查 |
| `draper/llvm_bridge.cpp` | LLVM CFG 导出 / 插桩计数桥接 |
| `run_draper.py` | 主实验驱动 |
| `run_real_pgo.py` | 真实硬件采样下的 PGO 对比 |
| `analyze_remarks.py` | 编译优化记录分析 |
| `build_dataset.py` / `validate_dataset.py` | 数据集构建与校验 |
| `prepare_*.py` | 各基准的预处理（Embench / cBench / LLVM test-suite） |
| `measure_runtime.py` | 运行时测量 |
| `fixtures/` | 小型 C 测试夹具 |
| `tests/` | 单元测试 |
| `draper_typeset/` | 论文排版工具链（Markdown → 预览） |

详见 [DRAPER.md](DRAPER.md)。

---

## 基准来源

[Embench 2.0rc2](https://github.com/embench/embench-iot/tree/embench-2.0rc2) —
学术界与工业界合作维护，面向嵌入式系统，许多程序源自 BEEBS。
2.0rc2 在上游 README 中标为开发候选版本。原版源码、许可证与自带结果检查函数全部保留。

本仓库**不包含**基准源码、数据集和实验产物（体积大，可按上述脚本重建）。

---

## 数据处理约定

- `data/` 与实验输出**不入版本库**（见 `.gitignore`），需本地重建
- 参考计数不应直接当作"精确二进制块标签"训练模型
- 数据集按程序分组划分，保证可复现

---

## 许可证

代码见 [LICENSE](LICENSE)（若无则默认保留所有权利，请联系作者）。
第三方基准与论文材料遵循各自原许可证。
