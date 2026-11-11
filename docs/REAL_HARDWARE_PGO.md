# 真实硬件采样、公开基准与本地编译结果

实验日期：2026-11-07。随机种子统一为 42。

## 1. 当前做到哪一步

此前 Embench 和 LLVM BenchmarkGame 已经过实际 Clang 编译、插桩执行和反馈优化；
DRAPER 所用的采样观测来自精确计数生成的 Poisson 模拟。
这些数据方便检查计数恢复和选点算法。真实硬件带来的地址映射缺失、周期采样偏差和相关性，
由本次新增的 LBR 实验开始覆盖。

本次完成了两项本地实验：

1. **正式发行的 bzip2 1.0.8 + Canterbury Large Corpus**：实际 LBR 采样、LLVM SamplePGO、
   插桩 PGO、正确性检查和运行时间测量。
2. **Hydra（OOPSLA 2026）的 cBench bzip2e**：从作者仓库下载源码与前三组原始输入，
   实际编译并验证压缩结果。

第一项建立真实采样的编译器对照；第二项确认论文使用的程序与输入可以直接在本机使用。
本次性能表中的恢复算法是 LLVM 自带的 profi 与频次传播。DRAPER 接入真实 LBR 数据是下一步。

## 2. 应当复用哪些数据

**复用公开程序、输入和参考输出，自行生成当前机器与编译器对应的 profile、CFG 和映射数据。**
程序版本、编译参数、内联和循环变换会改变 CFG；已有论文的计数适合复现其原始实验，
我们的 LLVM 23 实验则随构建重新采集。

| 来源 | 论文与用途 | 可复用内容 | 本阶段安排 |
|---|---|---|---|
| cBench / Hydra | OOPSLA 2026，CCF-A；研究优化前后 profile 传播 | 作者仓库里的程序、多个输入、编译与计数生成脚本 | 优先扩展为多程序、多输入实验 |
| SPEC CPU 2017 | POPL 2022 profi、ASPLOS 2023 Propeller，均为 CCF-A | 获得授权后使用源码、标准输入与运行工具 | 有实验室授权时加入 |
| Clang、GCC、MySQL 等开源应用 | profi 与 Propeller 的大型应用实验 | 开源程序；为本地实验准备编译任务或数据库负载 | 第二阶段先选 Clang 编译任务 |
| bzip2 1.0.8 + Canterbury | 实际发布的软件与公开压缩语料 | 官方发行源码、文本和序列数据 | 本次真实硬件实验已完成 |
| LLVM test-suite / Nisse | CC 2024；已有多类 C/C++ 测试程序 | 程序、部分输入和预期输出 | 与已有 LLVM 数据共同保留 |

前三行的论文关联来自论文实验章节。bzip2 1.0.8 与 Canterbury 这一组合是我们本次的本地应用实验，
与 Hydra 仓库中的 cBench bzip2e 分别记录。cBench 提供多输入编译研究基准；
Clang、MySQL 等进一步覆盖大型应用的代码规模和复杂工作负载。

### Hydra 数据的实际情况

论文 §5 使用 cBench 的 32 个程序，每个程序 20 组输入；其中 17 个程序成功编译和执行。
论文工具链为 LLVM 18.1.8，插桩工具为 Nisse。多输入很适合检验输入变化后的 PGO 效果。

已检查作者 GitHub 仓库中的源码、输入目录和 Ground_Truth 生成脚本。
当前下载范围包括源码、原始输入和脚本；已有计数 JSON 的下载位置仍待确认。
仓库 README 中部分目录说明与当前目录结构有差异，后续按实际文件列表取数据。
Zenodo 的整体归档此次网络访问失败，GitHub 文件下载成功。

cBench bzip2e 的输入说明列出了 32 组输入，论文实验采用每程序 20 组。
本次取前三组原始文件，保留原始编号与来源路径：

| 编号 | 原始输入 | 大小 | 本地检查 |
|---|---|---:|---|
| 1 | automotive_qsort_data/1.dat | 1,072,593 字节 | 通过 |
| 2 | automotive_qsort_data/16.dat | 117,074 字节 | 通过 |
| 3 | automotive_susan_data/12.pgm | 1,202,096 字节 | 通过 |

下载文件共 2,607,333 字节，其中源码、许可证和构建说明为 215,570 字节。
编译后整个目录占用约 **3.4 MiB**。保留原版包装函数，将每个输入的循环次数设为 1，
做首次编译和正确性检查；上游重复次数 40、350、110 单独保存在结果文件中。
检查方法是独立 Python bz2 解压新编译程序的输出，并与原始输入逐字节比较。

作者仓库另有 32 个 bzip2 压缩输入文件，总计约 16.05 MiB；该数字只描述这个压缩文件目录。
整个 cBench 套件的下载量仍需汇总共享输入目录后测定。

## 3. 本机真实硬件与采集方法

- CPU：Intel Core Ultra 7 255HX，20 个逻辑 CPU。
- 采样使用性能核 CPU 2，PMU 为 `cpu_core`，LBR 深度 32。
- Clang/LLVM 23.1.4；用户目录中的 perf 7.3.1；内核 7.2.11-1-cachyos。
- `perf_event_paranoid=2`，使用用户态事件 `cpu_core/cycles/u`。
- 所有编译与测量顺序执行，硬件实验固定 CPU 2，调度优先级 nice 10。
- perf 及依赖解包在项目的 `data/tools/perf-runtime` 中；实验全部在本机运行。

实际流程：

```text
原版 bzip2 源码
  → O3 + pseudo-probe 编译
  → 压缩 bible.txt 时用 perf 收集 cycles 与 LBR
  → llvm-profgen 结合二进制和 pseudo-probe 生成采样 profile
  → Clang 读取 profile，分别启用 profi 与频次传播
  → 检查压缩结果，测量新二进制的运行时间
```

pseudo-probe 保存编译前后的位置对应信息；LBR 保存近期执行过的分支地址。
llvm-profgen 结合这两类信息，把机器地址上的样本转成编译器可用的函数与代码位置权重。

采样命令的关键参数：

```bash
perf record -e cpu_core/cycles/u -c 100000 -j any,u \
  -o perf.data -- baseline/bzip2 -9 -c inputs/bible.txt
```

本次得到 **9,434 个实际硬件样本**，原始 `perf.data` 约 7.337 MB。
LLVM 生成的优化后 IR 中，profi 版本包含 267 个分支权重元数据节点，
频次传播版本包含 285 个，插桩 PGO 版本包含 388 个。
这些节点说明 profile 已经被编译器读取并参与编译；节点数随优化后的 CFG 改变。

另一路使用 `-fprofile-generate` 编译并执行相同训练输入，再用 `-fprofile-use` 编译插桩 PGO 版本。
四个最终版本均通过 3 组上游压缩参考输出检查，以及 3 个真实语料的压缩、独立解压和自身解压检查。

## 4. 本次运行时间

训练输入为 `bible.txt`；`world192.txt` 和 `E.coli` 留作测试输入。
每个输入交错运行四个版本，共 9 轮；版本顺序种子为 42。
测量压缩进程的墙钟时间，压缩输出写入 `/dev/null`。
加速按 `T_O3 / T_优化版 - 1` 计算，报告配对几何平均与 bootstrap 95% 区间。

| 输入 | SamplePGO + profi | SamplePGO + 频次传播 | 插桩 PGO |
|---|---:|---:|---:|
| bible.txt（训练） | −0.07% [−4.01, 2.91] | −6.82% [−15.63, −0.54] | −0.37% [−6.40, 3.96] |
| world192.txt（测试） | +1.30% [−1.66, 4.70] | +0.47% [−2.42, 4.06] | +1.78% [0.28, 3.70] |
| E.coli（测试） | +0.69% [−0.97, 2.41] | +0.28% [−0.29, 1.10] | +1.05% [−0.13, 2.35] |

两组测试输入上的改善幅度较小；除 world192 的插桩 PGO 外，区间都跨过零。
训练输入上频次传播版本变慢，profi 相对它加速约 7.25%，区间为 [0.80, 18.22]%。
这轮结果回答了“本机能否实际采样并反馈编译”：整个过程已经成功。
算法性能比较接下来增加程序、独立采集轮次和测试输入，并同时观察编译决策变化。

这些区间反映本轮重复计时的波动，采样 profile 本身只采集了一次。
当前设置为 O3、关闭 LTO，与论文版本和完整实验设置分别记录。

## 5. 按老师的方案，接下来构建什么

建议继续构建**真实 profile 配对数据**，程序和输入沿用公开基准。

1. **扩展 cBench 多输入组。** 先逐个验证论文成功运行的程序，再收集每程序 20 组输入；
   按程序划分算法开发与测试数据，并在程序内部保留 PGO 训练输入和测试输入。
2. **保存真实观测和对应信息。** 每个程序/输入保存二进制、编译命令、pseudo-probe、
   原始 LBR、采样 profile、相同 IR 阶段的 CFG 和插桩计数。
3. **接入 DRAPER。** 从 LLVM 的采样 profile 加载位置提取已有观测与缺失位置，
   与 CFG、插桩参考计数建立对应，再运行流约束恢复与风险选点。
4. **处理真实采样误差。** cycles 采样包含时间偏差，LBR 中的连续分支存在相关性；
   用独立采集和插桩参考数据估计误差尺度，并测量恢复分布与决策风险的可靠程度。
   现有 Poisson 模拟数据继续用于控制变量实验。
5. **比较实际收益和成本。** O3、LLVM profi、插桩 PGO、DRAPER、随机选点使用相同程序与输入；
   对补充插桩方法统一预算，报告运行时间、采集开销、代码大小、计数误差和内联等决策变化。

老师方案中的变换对应关系、决策风险和追加测量，都需要这些配对记录。
我们可以直接复用公开工作负载，研究精力主要投入真实观测、编译器接入和算法评价。

## 6. 存储与重跑

| 内容 | 本次磁盘占用 |
|---|---:|
| bzip2 真实 PGO 的二进制、IR、优化记录、输入、profile、日志 | 253 MiB |
| 用户目录 perf 及运行依赖 | 30 MiB |
| 源码下载、语料、先行试验与调查记录 | 28 MiB |
| cBench bzip2 源码、三组输入与检查结果 | 3.4 MiB |

加上工具压缩包，本轮新增内容约 320 MiB。检查时本地剩余约 27 GiB。
现有空间足够继续逐步扩展 cBench；先预留 5 GiB，根据各程序实际样本量增加预算。
Propeller 论文完整实验要求约 30 GB，单独安排空间更合适。

```bash
# 新建另一个输出目录，保留已完成的结果。
python3 run_real_pgo.py --output data/real-pgo-bzip2-v2
python3 prepare_cbench_bzip2.py --inputs 20 --output data/cbench-bzip2-hydra-20inputs
```

第一条使用已经下载的 bzip2、Canterbury 输入和本地 perf；第二条从作者仓库下载前 20 组原始输入。
第二条还会增加输入下载量与运行时间。已执行的 cBench 结果对应默认的前三组输入。

结果目录：`data/real-pgo-bzip2-v1/summary.json` 和 `data/cbench-bzip2-hydra-v1/summary.json`。
硬件实验全部命令保存在前者目录下的 `commands.json`，日志位于 `logs/`。

## 参考来源

- Hydra，OOPSLA 2026，§5：[论文 DOI](https://doi.org/10.1145/3798247)、
  [作者仓库](https://github.com/lac-dcc/hydra)、[归档](https://doi.org/10.5281/zenodo.18376755)。
- Profile Inference Revisited，POPL 2022，§5：[论文 DOI](https://doi.org/10.1145/3498714)。
- Propeller，ASPLOS 2023，§5 与附录：[论文 DOI](https://doi.org/10.1145/3575693.3575727)、
  [公开实现](https://github.com/google/llvm-propeller)。
- Nisse，CC 2024：[论文 DOI](https://doi.org/10.1145/3640537.3641569)。
- [bzip2 1.0.8 官方源码](https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz)。
- [Canterbury 语料介绍](https://corpus.canterbury.ac.nz/descriptions/)、
  [Large Corpus 下载](https://corpus.canterbury.ac.nz/resources/large.tar.gz)。
