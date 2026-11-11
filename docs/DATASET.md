# DRAPER 配对数据集

2026-11-10 更新：主语料已扩大为 LLVM test-suite 23.1.5 的 **38 个应用家族、40 个可执行工作负载、120 组模拟采样配对**，源码累计 594,381 行。普通版本及两轮插桩版本全部通过上游检查，全部非零计数已对应到编译中间表示。服务器位置、程序划分、计数含义和验证结果见 [当前主数据集说明](LARGE_DATASET_2026-11-10.md)。下文保留原来的 26 个小程序数据说明。

2026-11-09 核对补充：本文的 26 个程序指已完成配对数据采集；最近 node3 的完整恢复与性能比较只用了其中 6 个程序。下表中的 slre 原被划为测试程序，但已参与反复开发分析，今后应按开发程序处理。原划分保留为历史记录，新的独立评价需另行指定未参与开发的程序和输入。详细差距见 `TEACHER_IDEA_AUDIT_2026-11-09.md`。

第一批已完成 Embench 2.0rc2 的全部 19 个程序，以及 LLVM test-suite 21.1.0 中的 7 个 BenchmarkGame 程序。
合计 **26 个程序、78 份阶段图、234 组配对记录**，两批完整数据约 **341 MiB**。实验工具统一使用随机种子 **42**。
每个程序使用上游内置工作负载，在 early、O1、O3 三个阶段采集自己的精确计数，再产生三种采样率的观测。

## 数据规模与划分

Embench 数据位于 `data/dataset-embench-v2`：

| 内容 | 数量 |
|---|---:|
| 独立程序 | 19 |
| 程序与优化阶段组合 | 57 |
| 采样与精确计数配对记录 | 171 |
| 各阶段累计基本块 | 20,540 |
| 各阶段累计控制流边 | 29,670 |
| 早期 IR 伪探针来源记录 | 5,567 |
| 磁盘占用 | 约 328 MiB |

171 条记录来自 19 个程序的多种设置。分析泛化能力时，以程序为单位统计。

| 集合 | 程序数 | 配对记录数 | 程序 |
|---|---:|---:|---|
| 训练 | 13 | 117 | aha-mont64、crc32、depthconv、edn、md5sum、nettle-aes、nettle-sha256、picojpeg、qrduino、sglib-combined、ud、wikisort、xgboost |
| 验证 | 3 | 27 | matmult-int、nsichneu、tarfind |
| 测试 | 3 | 27 | huffbench、slre、statemate |

同一程序的所有阶段和采样设置都留在同一集合。公共支持文件中的函数保留在程序图中，以维持调用关系；
`train_mask` 指定用于学习的 benchmark 函数。这批划分用于算法开发，后续应用级评测按程序家族另设测试集。

## 每个样本包含什么

入口为 `samples.jsonl`。每行记录程序、阶段、集合和以下文件的位置：

- `graph.json`：CFG、调用关系、循环深度、指令数量、操作码直方图、源码位置和内联来源。
- `observations-1-16.json`、`observations-1-64.json`、`observations-1-256.json`：采样率分别为 1/16、1/64、1/256 的 Poisson 观测，隐藏比例 15%，种子 42。
- `exact-0.json`、`exact-1.json`：在相应 IR 阶段插桩，执行两次得到的精确基本块及边计数。
- `labels.json`：训练标签，包括精确次数、后继概率、80% 热分支判断和 `train_mask`。
- `reference.decisions.json`：把参考计数写回 LLVM 后得到的实际 BPI 判断。
- `reference.remarks.yaml`：参考计数编译产生的优化记录，包括内联、展开、向量化等。

同一程序目录还保存：

- `canonical.ll` 和各阶段 IR，以及相应可执行文件。
- `probes.ll`、`probe-origins.json`、`probe-binary`、`probe-sections.txt`、`probe-disassembly.txt`：伪探针、早期块编号、优化后二进制和反汇编。
- `O1/transformations.yaml`、`O3/transformations.yaml`：由早期 IR 产生相应优化阶段时的变换记录。
- `O1/early-candidates.json`、`O3/early-candidates.json`：通过共同源码位置获得的跨阶段候选关系。
- `commands.json` 与日志：编译、执行和 profile 处理的完整命令及返回码。

`source/` 保存本次使用的源码，`implementation/` 保存采集时的脚本及 C++ 实现。
输入观测与精确标签分别保存，恢复算法只接收观测、图和明确选择的少量实测计数。

## 与老师方案的对应关系

| 老师方案所需内容 | 当前数据 | 接下来的工作 |
|---|---|---|
| 精确 profile 与采样 profile 配对 | 三个 IR 阶段各有精确计数和三种模拟采样 | 接入真实 PMU 采样 |
| 变换前后程序图 | early、O1、O3 图及各自运行次数 | 利用伪探针、内联栈和变换记录建立更精确的关系 |
| 图模型输入特征 | CFG、调用、循环、操作码、源码位置 | 增加与决策相关的上下文特征 |
| 决策标签 | 精确计数下的热分支判断和 LLVM 优化记录 | 提取内联成本、阈值及候选匹配，关联运行时间 |
| 主动插桩数据 | `data/draper-seed42-v2` 中的风险、随机、频次选点及实测次数 | 将选点收益与实测开销一起建模 |

当前观测来自精确计数的 Poisson 模拟。跨阶段的源码对应关系标记为候选；
在 14,973 个优化后基本块中，14,807 个找到候选，12,635 个对应多个候选。
这个结果提示：源码行号能缩小查找范围，伪探针和变换语义需要继续参与消歧。

## 构建与检查

```bash
python3 build_dataset.py --output data/my-embench-dataset
python3 validate_dataset.py data/my-embench-dataset
```

构建器逐个程序执行，编译和数值库使用单线程，保留至少 5 GiB 空闲空间。
每条外部命令限时 90 秒，每个程序的采集任务限时 900 秒。
再次执行同样配置可使用 `--resume` 跳过成功程序；代码或配置变化时使用新的输出目录。
失败程序的日志和部分结果保留在原目录中。

检查脚本验证：

1. 同阶段的两次精确计数相同。
2. 精确计数满足 CFG 和可见内部调用约束。
3. 观测可由种子 42、指定采样率和精确计数重现。
4. 同一程序位于同一集合，标签匹配对应的图和计数。
5. 伪探针来源记录、编译决策和优化记录存在且对应。

Embench 当前检查结果为 `data/dataset-embench-v2/validation.json`，全部通过。

v2 更新了插桩后函数及调用点的内存属性，确保原先标记为纯函数的代码也保留计数器写入。
递归单元测试验证了 109 次调用对应的完整入口计数。
在真实数据中，这项修复恢复了 wikisort 的 O1 阶段 `TestingAscending` 函数的 1,200 次执行计数，并修正了递归程序的次数。
当前训练和分析使用 v2 目录。

## LLVM test-suite 补充数据

源码来自 LLVM 官方仓库的 `llvmorg-21.1.0`，选取 BenchmarkGame 的 7 个程序：
fannkuch、n-body、nsieve-bits、partialsums、puzzle、recursive、spectral-norm。
下载内容包括原始源码、上游参考输出、构建说明和许可证。

该批已完成 21 份阶段图、63 组配对记录，共 729 个基本块、1,030 条边，磁盘占用约 13 MiB。
数据位于 `data/dataset-llvm-game-v2`，检查结果为 `validation.json`，全部通过。
训练程序为 fannkuch、nsieve-bits、puzzle、recursive、spectral-norm；验证程序为 partialsums；测试程序为 n-body。

```bash
python3 prepare_llvm_sources.py
python3 build_dataset.py --suite llvm-game \
  --source data/sources/llvm-test-suite-21.1.0-subset/SingleSource/Benchmarks/BenchmarkGame \
  --license-file data/sources/llvm-test-suite-21.1.0-subset/LICENSE.TXT \
  --output data/my-llvm-game-dataset
python3 validate_dataset.py data/my-llvm-game-dataset
```

每个执行版本与上游参考输出比较，文字逐项一致，浮点数采用相对误差 `1e-7`、绝对误差 `1e-8` 的容差。
实际完成程序、失败日志与样本数量写入对应数据目录的 `summary.json`。

## 下一步的规模

当前数据适合检查图特征、标签读取和计数先验的训练过程。
扩展时优先增加多种输入的 LLVM test-suite 应用、PolyBench 和真实应用家族，保留相同的精确计数和观察记录。
多输入程序的所有输入仍随程序一起划分集合。
