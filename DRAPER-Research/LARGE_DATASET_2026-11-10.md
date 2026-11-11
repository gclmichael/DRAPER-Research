# 当前主数据集

LLVM 是本项目使用的编译器基础设施名称；MiBench 是面向嵌入式应用的公开基准套件名称。文中的“工作负载”指一个可执行程序及上游指定输入，“应用家族”把同一应用的编码器、解码器等放在一起。

主数据集位于 node3 的 `/data/chaoni/chengli/draper-node3/data/industrial-corpus-v1`。它取自 LLVM test-suite 23.1.5，包含 MultiSource/Applications、MultiSource/Benchmarks/MiBench、MultiSource/Benchmarks/Ptrdist 三部分。

## 已构建规模

| 内容 | 实际数量 |
|---|---:|
| 应用家族 | 38 |
| 可执行工作负载 | 40 |
| 编译命令涉及的源码文件路径 | 610 |
| 这些源码文件的累计行数，含注释和空行 | 594,381 |
| 单项达到一万行源码的工作负载 | 12 |
| 普通版本上游正确性检查通过 | 40/40 |
| 两轮插桩版本上游正确性检查通过 | 80/80 |
| 两次计数完全相同的工作负载 | 35/40 |
| 保存的函数计数记录，跨工作负载累计 | 15,464 |
| 稀疏计数器槽位，跨工作负载累计 | 132,297 |
| 已对应到中间表示的计数更新位置 | 110,051 |
| 其中采用变量增量的位置 | 980 |
| 三种采样率的配对记录 | 120 |

源码路径不同仍可能含相同或近似代码，因此累计源码行数不代表去重后的代码量。120 条记录来自同一批 40 个工作负载；三种采样率和重复执行均不增加独立程序数量。

| 较大应用 | 当前工作负载源码行数 |
|---|---:|
| SQLite | 86,632 |
| SPASS | 69,104 |
| ClamAV | 66,762 |
| Ogg Vorbis 编码器 | 58,356 |
| JM 视频编码器 | 57,035 |
| kimwitu++ | 56,084 |
| MiBench 排版程序 | 38,919 |
| JM 视频解码器 | 28,699 |
| MiBench JPEG | 22,960 |
| MiBench LAME | 19,298 |
| Lua | 13,413 |
| DParser | 12,329 |

这些是编译器测试套件固定的工作负载版本。例如其中 SQLite 为 **3.5.7**，适合可重复的编译器研究，不能写成当前生产版本 SQLite 的完整评价。

## 来源与论文依据

### 新增 PolyBench 公开套件

`data/polybench-corpus-v1` 来自同一份 LLVM test-suite 中的 PolyBench：27 个可执行内核，编译源码累计 6,121 行。开发、验证、测试各为 17、5、5 项，划分在执行前固定。27 项普通版本和 54 次插桩执行均通过上游输出检查；两轮计数一致，81 条模拟采样配对记录通过索引验证。它们是循环内核，不计作大型工业应用。

本次保留上游默认问题规模。上游 `TEST_SUITE_BENCHMARKING_ONLY` 配置排除 gemm、2mm、3mm，因此这一构建是 27 项，不能写成完整的 30 项。已构建与已完成新算法实验的数量分别报告。现代 SQLite、zstd、FFmpeg 的源码和独立评价输入另存，不混入 PolyBench 的数量。

### 新增现代应用

三个应用均已完成普通构建、独立评价输入的参考输出生成、两轮 LLVM 插桩运行和输出验证：

| 应用 | 编译源码行数 | 采集输入 | 独立性能评价输入 |
|---|---:|---|---|
| SQLite 3.54.1 | 311,070 | 内存数据库，20 万行、索引、分组、连接、排序 | 30 万行，改变分组数量 |
| zstd 1.5.7 | 43,441 | 压缩 LLVM Applications 源码，等级 3 | 压缩 MiBench 源码，等级 5 |
| FFmpeg 8.0.1 | 136,356 | 1280×720 测试视频，90 帧，缩放至 640×360 | 1920×1080 测试视频，120 帧，缩放至 1280×720 |

源码规模按实际编译的 C 文件统计，包含生成源码；不代表运行时执行了所有代码。FFmpeg 使用视频生成、缩放和逐帧校验任务，未覆盖全部编解码器。zstd、FFmpeg 使用 C 实现以支持 LLVM 插桩，构建参数与参考程序保持一致。

数据位于 `data/teacher-apps-v1`。现代 SQLite 与旧 SQLite 属同一家族，都放在开发集合；zstd、FFmpeg 在执行前指定为测试集合。现共有 70 个已构建工作负载：原 40 项、27 个 PolyBench 内核和 3 个现代应用。全块采集及新算法性能实验的完成数量仍须查看逐项结果表。

LLVM 官方文档将 test-suite 用作程序正确性和性能测试，其中 MultiSource 包含完整应用。[LLVM Test Suite Guide](https://llvm.org/docs/TestSuiteGuide.html)

MiBench 原论文为 Guthaus 等人的 *MiBench: A Free, Commercially Representative Embedded Benchmark Suite*，发表于 2001 年；本数据集使用 LLVM 维护的相应工作负载。[MiBench 项目与论文入口](https://vhosts.eecs.umich.edu/mibench/)

老师方案第 16 节明确列出 LLVM test-suite、MiBench 和 SQLite。本次选择与这三项一致。第 20 节最小版本还要求 PolyBench、SPEC CPU 2017、zstd 和 FFmpeg。当前优先完成该节公开套件，SPEC 因暂无授权材料按用户安排暂缓。第 16 节另外列出的 Clang、数据库系统和并行套件尚未全部覆盖，不能将最小版本写成已完成全部候选套件。实时完成数量见 `data/public-results-v1/results.md`。

## 文件与计数含义

- `source.tar.xz`：保存的上游源码归档，许可证保留在归档内。
- `configuration.json`：执行前固定的程序家族划分，随机种子 42。
- `native/`、`instrumented/`：普通及插桩构建目录，包括输入、参考输出、可执行文件、编译命令和保存的编译中间表示。
- `profiles/`：每个工作负载两次实际运行的原始合并计数及文本表示。
- `paired-records-v2/summary.json`：当前配对索引；`paired-records-v2/samples.jsonl` 为样本入口，里面的路径以数据集根目录为基准。
- 每个配对目录的 `exact-counters.json`、`counter-locations.json`、`unit-*.ll`：计数、更新位置及编译前端中间表示。
- `observations-1-16.json`、`observations-1-64.json`、`observations-1-256.json`：三种泊松模拟采样，随机隐藏 15% 的位置。明确标为模拟观测。
- `paired-records-v2/validation.json`：实际数据检查结果。

这里使用 LLVM 原生的稀疏插桩。一个计数器可能在多个位置更新；变量增量计数器每次增加的值也可能不是 1。索引保留这两类区别。不能直接把所有计数槽位当作基本块执行次数。


22,473 个槽位在保存的中间表示中没有更新位置，全部为零；全部非零计数均有对应位置。保留这些槽位以维持编译器原始编号。

两次计数有变化的五项为 JM 编码器、JM 解码器、DParser、kimwitu++ 和 Lua。JM 的变化位于报告函数，其他变化也已定位到函数记录。两个运行的计数都保留；模拟观测与第一轮精确计数配对，不用“两个运行相等”替代每次运行的结果检查。

## 开发与评价划分

按应用家族固定划分：开发 26 个工作负载、验证 7 个、测试 7 个；同一应用的编码器与解码器没有跨集合。开发家族数为 24，验证和测试各 7。

验证集：SPASS、aha、kimwitu++、viterbi、MiBench typeset、patricia、CRC32。

测试集：lambda、Lua、minisat、MiBench SHA、GSM、Ptrdist anagram、bc。

本次全量采集和正确性检查涵盖所有集合；核心算法接入调试使用开发集 spiff。最终评价还应增加每个应用的独立输入，这批上游默认输入的重复执行不构成输入泛化实验。

## 重建与使用

服务器环境在 `/data/chaoni/chengli/tools/draper-env`。设置其 `bin` 到 `PATH`、`lib` 到 `LD_LIBRARY_PATH`，并将数值库线程数设为 1 后执行：

```bash
python build_large_corpus.py --source data/large-sources/test-suite-23.1.5.src --output data/new-corpus
python index_large_corpus.py data/new-corpus --name paired-records-v2
python validate_large_corpus.py data/new-corpus --index paired-records-v2
```

完整流程使用单个编译任务。上游父目录测试配置被显式复制到构建目录，以保留输出与退出码检查；输出检查规则没有放宽。旧程序的隐式函数声明警告按套件兼容设置处理，命令保存在 `commands.json`。

现有恢复器使用全块控制流图。原 40 项现已全部转换为全块输入，anagram 和 Lua 的非局部返回已单独处理。早期计数对照与优化后观测方法分别运行、分别汇总；完成采集不等于完成新算法性能评价。具体状态见 `data/public-results-v1/results.md`，观测方程和区间实验见 `OBSERVATION_MODEL_2026-11-10.md`。
