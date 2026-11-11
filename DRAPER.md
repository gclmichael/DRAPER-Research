# DRAPER 原型

## 如何运行

依赖本机 LLVM 23 开发工具、Python、NumPy 和 SciPy；编译优化记录分析使用 PyYAML。
Embench 源码使用已有的 `data/embench-source/embench-iot-embench-2.0rc2`。

```bash
python3 -m unittest discover -s tests -v
python3 run_draper.py \
  --programs crc32 slre qrduino nettle-aes picojpeg wikisort \
  --rate 0.015625 --missing 0.15 --bootstrap 20 --anchors 3 \
  --seed 42 --output data/my-draper-experiment
python3 analyze_remarks.py data/my-draper-experiment
```

每次指定新的输出目录，脚本拒绝覆盖已有实验。编译和执行均顺序进行，
数学库使用一个线程，每个外部命令限时 90 秒。运行地点是本地。

## 代码分别做什么

| 文件 | 工作 |
|---|---|
| `draper/llvm_bridge.cpp` | 读取 LLVM IR，导出 CFG、循环深度、调用位置，插入计数器；调用 LLVM profi；写回 profile 并记录 BPI 判断 |
| `draper/model.py` | 建立稀疏流约束，恢复计数，计算 bootstrap 区间，识别分支风险并选择追加插桩的位置 |
| `run_draper.py` | 编译 Embench，收集计数，运行各算法，再编译并执行所有结果版本 |
| `analyze_remarks.py` | 读取 LLVM 内联、展开、向量化记录，保留成本和阈值，比较成功变换事件 |
| `tests/test_model.py` | 检查流约束、缺失观测、补充计数、风险判断和输入一致性 |
| `tests/test_llvm_bridge.py` | 实际编译并执行含重复 switch 目标的程序，检查计数映射、调用次数、稀疏插桩和 LLVM profile 消费 |
| `tests/test_remarks.py` | 检查优化记录中的成本、阈值及重复出现的事件 |

## 数据如何得到

1. Clang 产生早期 IR，使用 `llvm-link` 合并当前程序及支持代码。
2. 执行 mem2reg、SimplifyCFG 和一轮 InstCombine，再拆分关键边。
3. 保存此时的 `canonical.ll`。同一份 IR 用于精确计数、恢复结果回写和各版本编译。
4. 为每个基本块放置一个 LLVM 计数器，执行 Embench 自带输入两次。
5. 读取计数器，核对两次计数，并检查 CFG 和可见内部调用的流守恒。
6. 从这些计数产生已知采样率的 Poisson 观测，随机隐藏指定比例的基本块。

`observations.json` 只包含观测值、可见位置和采样率。求解函数接收这些观测、CFG 和 profi 初值。
参考计数用于产生模拟观测及评价误差；稀疏插桩另行编译并执行，只获取选中位置的次数。

### 为什么基本块计数可以给出边计数

关键边是“起点有多个后继、终点有多个前驱”的边。拆分之后：

- 起点只有一个后继时，该边次数等于起点的次数。
- 否则，终点只有一个前驱，该边次数等于终点的次数。
- 函数入口、出口各补一条虚拟边，用于统一表达流守恒。

图文件为每条边保存对应的计数器编号。单元测试特意覆盖多个 switch 分支共享目标的情况，
防止这些边被错误地合并成一条。这里使用全基本块插桩获取离线参考值，独立于 LLVM 原有的生成树插桩。

## 恢复算法

变量 `x` 是每条 CFG 边的执行次数，`B x` 是基本块次数。`B` 取每个块所有流入边之和。
采样率为 `q`，观测为 `s`，目标为：

```text
最小化：Σ Huber((q × Bx − s) / sqrt(s + 1)) + 弱二次正则
约束：  x ≥ 0
        每个基本块的流入次数 = 流出次数
        符合封闭调用条件的内部函数：入口次数 = 调用点次数之和
```

计数先归一化，避免热循环与低频入口的数值量级差异过大。
Huber 损失用迭代重加权实现；每一步采用 ADMM 求解稀疏二次问题。
最后用最小 L1 改动消除浮点流残差，并再次检查非负性和各条等式。

初值和弱正则中心来自当前 LLVM 的 `applyFlowInference`，即 profi 的流求解实现。
比较双方使用完全相同的 CFG 和观测。这里没有复现整个 SampleProfileLoader 的所有启发式。
当前正则来自同一份观测的 profi 结果，属于数据驱动的经验正则。

外部函数、地址被取用的内部函数保留开放调用边界。C++ 异常控制流等不支持的终结指令会明确报错。
本轮运行的是单线程程序；计数器采用 LLVM 的普通非原子更新。

## 风险如何进入编译器

第一种实际接入的判断是 LLVM `BranchProbabilityInfo::isEdgeHot`：分支概率严格大于 80%。
对恢复后的计数做带 0.5 平滑项的参数 bootstrap，记录分支概率的分位数区间：

- 区间跨越 80%：标为 risky。
- 入口观测不足或重复恢复出现零流量：标为 unknown。
- 其余位置：标为 stable。

20 次 bootstrap 用于初期调试。报告直接给出这一次实验中参考计数落入区间的比例，
后续以实测覆盖率检验名义 95% 区间的准确程度。

编译对照包含：

| 版本 | 写入 LLVM 的信息 |
|---|---|
| baseline | 同一份 canonical IR，不添加 profile |
| reference | 精确参考频次 |
| profi | LLVM profi 恢复的频次 |
| restored | 流约束 Huber 恢复的频次 |
| risk-gated | 同样的恢复频次，只给 stable 分支写入权重；其余分支使用 LLVM 静态估计 |
| uniform-conservative | 保留恢复的函数入口次数，所有分支都使用静态估计 |
| calibrated | 实测少量选中位置的执行次数，再重新恢复频次 |

LLVM 读取 `!prof` 元数据后，工具调用实际 BPI 分析，保存每条分支的概率与热点判断。
随后 Clang 继续 O3 编译，执行原版 `verify_benchmark` 检查结果。
决策报告同时统计所有分支和实际执行过的分支，避免把未执行路径混进主要比较。
另外读取 LLVM 自己输出的内联、循环展开和向量化记录。内联记录包含实际的成本与阈值。
同一源码位置可能有多个上下文，比较时保留事件出现次数；事件差异不直接解释为独立调用点的翻转率。

### 选择位置并追加插桩

用预测的翻转概率及恢复的执行频次给分支排序，每个程序最多选 K 个计数位置。
工具对这些基本块实际插桩，执行同一个内置输入，再把测得次数作为等式加入恢复问题。
记录静态计数器数和动态计数器更新总数。选择过程只读取恢复结果及风险分数。
每函数、每循环的独立预算，以及运行时间预算，留给后续实验扩展。

## 与导师方案的对应

| 导师方案部分 | 当前实现 | 下一步 |
|---|---|---|
| 变换证据图 | 早期 IR 的 CFG、循环、调用、计数器到边的精确关系；观测采用同一 IR 上的已知映射 | 加入真实二进制的 pseudo-probe、内联上下文、clone/merge/split 关系 |
| 观测模型 | 可复现的 Poisson 采样与缺失位置 | 在可用 PMU 的机器采集真实分支记录，测量事件偏差 |
| 流一致恢复 | profi、Huber 数据项、非负约束、CFG 与封闭内部调用约束 | 加入证据支持的变换比例变量和跨上下文约束 |
| 不确定性 | bootstrap、区间覆盖率、分支翻转概率 | 增加重复实验，改善区间覆盖率 |
| 决策风险 | 实际 BPI 热分支判断、风险回退、统一回退对照；读取真实内联、展开和向量化记录 | 将内联和布局成本接入风险目标；用运行时间评价决策损失 |
| 主动采集 | top-K 位置选择、实际稀疏插桩、再次恢复 | 比较更多预算及测量开销 |
| 图先验网络 | 使用 profi 经验正则 | 程序种类与训练标签足够后训练，并按程序划分测试集 |

## 如何读实验结果

每个程序的 `report.json` 包含计数误差、流残差、分支概率误差、LLVM 判断差异、
新增计数器数量和 bootstrap 区间覆盖率。`summary.json` 汇总全部程序。
`implementation/` 保存此次执行的代码，`commands.json` 和 `logs/` 保存编译与运行记录。

当前先看三个问题：恢复后的计数是否满足程序控制流；误差是否低于 profi；
风险回退是否真的减少关键判断差异。计数误差下降和判断差异减少分别报告，
运行时间收益将用单独的重复测量实验评价。
# 新实验入口

新实验的模拟采样、bootstrap、随机选点、程序划分和计时顺序统一使用种子 42。

```bash
python3 run_draper.py --programs crc32 slre qrduino nettle-aes picojpeg wikisort \
  --seed 42 --selection-controls --output data/my-seed42-experiment
python3 analyze_remarks.py data/my-seed42-experiment
python3 measure_runtime.py data/my-seed42-experiment --rounds 9 --seconds 0.2
```

`--selection-controls` 增加随机选点、频次选点，以及 profi 使用相同实测计数后做 L1 投影的对照。
配对数据在本地 `data/` 下构建（数据目录未纳入版本库），已测结果记录于内部笔记。
