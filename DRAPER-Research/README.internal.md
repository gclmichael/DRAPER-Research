# DRAPER 启动数据

## 最新：真实硬件采样与公开论文基准

2026-11-07 已在本机完成 bzip2 1.0.8 的真实 LBR 采样、LLVM SamplePGO 与插桩 PGO 编译，
并编译检查了 Hydra（OOPSLA 2026）使用的 cBench bzip2e 与前三组原始输入。
硬件条件、论文数据来源、性能结果和下一步数据构建安排见 [REAL_HARDWARE_PGO.md](REAL_HARDWARE_PGO.md)。
以下各节保留对应阶段的实验设置与存储记录。

## DRAPER 算法原型

已增加 LLVM CFG 导出、精确边计数、profi 对照、流约束恢复、分支风险回退和实际稀疏插桩。
代码说明及运行方法见 [DRAPER.md](DRAPER.md)。
第一轮 6 个 Embench 程序的结果与失败案例见 [RESULTS.md](RESULTS.md)。

## 已运行的公开基准：Embench 2.0rc2

来源是用户提供的 `embench-iot-embench-2.0rc2.zip`，对应
[Embench 官方仓库](https://github.com/embench/embench-iot/tree/embench-2.0rc2)。
Embench 由学术界和工业界合作维护，主要面向嵌入式系统，许多程序源自 BEEBS。
2.0rc2 在上游 README 中标为开发候选版本。原版源码、许可证和自带的结果检查函数全部保留。
本次抽取 6 个程序验证 PGO 步骤，没有运行完整套件的官方性能评分。

| 程序 | 工作内容 | profile 中的函数数，含支持代码 | 结果 |
|---|---|---:|---|
| crc32 | CRC 校验计算 | 18 | 通过 |
| slre | 正则表达式匹配 | 22 | 通过 |
| nettle-aes | AES 加解密 | 26 | 通过 |
| picojpeg | JPEG 解码 | 34 | 通过 |
| qrduino | 二维码生成 | 27 | 通过 |
| wikisort | 排序 | 40 | 通过 |

运行步骤：O3 编译并检查结果 → IR 插桩编译 → 分别运行两次 → 合并各次计数 →
PGO 重编译并检查结果 → 编译带 pseudo-probe 的版本并检查结果。
每个程序沿用原版 `main` 调用 `verify_benchmark`，失败时返回非零退出码。
12 次插桩执行生成 12 份原始 profile，同一程序两次执行的计数相同。
PGO 版本的 IR 包含函数执行次数和分支权重；共 54 份导出的 IR 文件通过 LLVM 检查。
基准内置输入用于所有这些步骤；输入变化、硬件采样和运行时间对比安排在后续实验。
计数包括初始化、预热和结果检查代码，并非只有计时区域。

本机未安装 SCons，因此脚本按上游 `sconstruct.py` 的源文件组成直接调用 Clang，
使用原版 `native/size` 支持代码，`WARMUP_HEAT=1`、`GLOBAL_SCALE_FACTOR=1`，关闭 LTO。
Clang 对原版支持代码中的 GCC 专用 `externally_visible` 属性发出忽略警告，记录保存在编译日志中。

```bash
python3 prepare_embench.py
```

默认读取 `data/embench-source/embench-iot-embench-2.0rc2`，结果在 `data/embench-v1`。
脚本拒绝覆盖已有结果；另一次实验可指定 `--output data/embench-v2`。
`summary.json` 保存每个程序的检查结果，`commands.json` 和 `logs/` 保存命令与输出。
每条命令限时 45 秒，顺序执行，不访问服务器。

本次实际大小：压缩包 3,469,887 字节（3.31 MiB）；解压源码逻辑大小 28,207,053 字节，
磁盘占用约 28 MiB；6 个程序的二进制、IR、profile 和日志占用约 15 MiB。
连同原压缩包合计不足 50 MiB。本地剩余约 16 GiB，足够继续扩充这个套件。

下一步添加 LLVM test-suite 的应用型程序，以及真实硬件采样。
Embench 可保留为小程序测试组；它的内置固定输入和较小程序规模不能单独代表大型应用 PGO。

## 已准备的 pilot-v1

这是由自编小程序组成的确定性语料，用于检查插桩采集和编译步骤。

- 6 类 C 程序：菱形分支、固定循环、提前退出循环、多调用上下文、内联候选、嵌套循环。
- 每类 2 组输入，共 12 组程序/输入记录。
- 每组独立采集 2 次 IR instrumentation profile，共 24 份 `.profraw`；重复计数一致。
- 每类保存 O3 baseline、插桩二进制、pseudo-probe 二进制、插桩前 IR 和插桩后优化 IR。
- 每组保存 `.profdata`、计数文本、PGO 二进制和优化记录。
- baseline、pseudo-probe、插桩和 PGO 程序输出均与独立 Python 计算结果核对；每个 PGO 二进制还验证了另一组输入的输出。
- `manifest.json` 保存记录、工具版本、文件大小和 SHA-256；`commands.json` 保存实际命令。

数据目录：`data/pilot-v1/`。数据不纳入 Git。

## 重要边界

`source_semantics_not_ir_labels` 保存源码执行语义的参考计数。
`.profdata` 保存 LLVM 插桩计数；显式的计数器到 CFG 边对应关系由新增 DRAPER 工具生成，见 `DRAPER.md`。
插桩前 IR 来自实际 `PGOInstrumentationGen` 之前的模块，已通过 `opt -passes=verify`。
`instrumented.optimized.ll` 是后续优化后的 IR，不能当成插桩时的 CFG。
此版本没有真实 `perf.data`、带噪合成标签、训练/测试划分或可信运行性能结论。
当前仅检查程序语义和管线；内联候选、循环等名称不保证最终编译器实施了相应变换。
两组输入均用于管线验证，不能据此声称泛化收益。

## 生成方式

在本地已有 Clang 和 llvm-profdata 的环境运行：

```bash
python3 prepare_pilot.py
```

脚本顺序执行、降低自身调度优先级、每条命令设置 30 秒超时，无第三方 Python 依赖。
已存在的数据目录会导致拒绝覆盖；重跑时先将旧目录归档，或修改数据版本目录。
失败运行的 manifest 标记为 incomplete，不得混入有效数据。
工具链必须兼容；本次使用 LLVM 23.1.4。命令记录包含本次机器的绝对路径，跨机器需重新运行脚本。
脚本直接使用本地已有的编译器和工具执行。

## 存储预算

2026-11-05 实测：有效数据共 159 个文件，逻辑大小 1,674,908 字节，`du -sh` 约 2.1 MiB。
这不包含已经安装的 LLVM 工具链。当前本地文件系统剩余约 17 GiB，足够本阶段使用。

| 阶段 | 建议预留 | 说明 |
|---|---:|---|
| 当前微型语料及小规模迭代 | 100 MiB | 本次实际约 2.1 MiB，不需申请大存储 |
| 少量 test-suite 程序和一个真实应用 | 5–10 GiB | 规划估算，取决于源码、输入和构建产物；不包括源码构建 LLVM |
| 第一轮真实采样实验 | 30–50 GiB | 规划估算，先做单次采集测定实际增长率 |
| 更高采样率、多应用或多版本扩展 | 100 GiB 起，按实测调整 | 暂不预先下载或占用 |

采样记录的数量级估算：

`运行次数 × 每次秒数 × 实际每秒样本数 × 平均记录字节数`

例如 10 个程序 × 2 个输入 × 3 种采样配置 × 5 次重复，共 300 次运行；
若每次 30 秒、平均每秒 1,000 个样本、平均记录 256 字节，记录主体约 2.3 GB；
若平均每秒 10,000 个样本，则约 23 GB。
这些是用于规划空间的情景估算。LBR 深度、调用栈、mmap 信息和事件配置都会改变结果；
Intel PT/ETM trace 不适用这个简单样本记录估算。还需另计二进制、调试信息、源码、输入和临时文件。

服务器 `gpu-101` 根文件系统此前检查仅剩约 1.8 GB，且未发现可见硬件 CPU PMU。
本阶段未向该服务器写入数据或运行任务。共享存储需先确认独立目录和资源配额，再安排后续实验。

## 下一步

先导出并验证插桩计数到 CFG 边的对应，再增加 LLVM test-suite 程序；硬件采样条件满足后补采真实样本。
不要把这里的参考计数直接作为“精确二进制块标签”训练模型。
# 新增配对数据与种子 42 实验

已采集 Embench 的 19 个程序和 LLVM test-suite BenchmarkGame 的 7 个程序，合计 234 组配对记录。
数据结构、划分和构建命令见 [DATASET.md](DATASET.md)，选点与计时结果见 [RESULTS_SEED42.md](RESULTS_SEED42.md)。
当前数据目录为 `data/dataset-embench-v2` 和 `data/dataset-llvm-game-v2`。
