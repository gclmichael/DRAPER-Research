# DRAPER 项目 Windows 续接说明

更新时间：2026-11-09。新实验的随机种子统一使用 42。

## 给 Windows ChatGPT 的第一条消息

解压项目后，在 ChatGPT 中附上本文件和 `CONVERSATION.md`，发送：

> 这是 DRAPER 编译优化研究项目。请先阅读 WINDOWS_HANDOFF.md、CONVERSATION.md、
> REAL_HARDWARE_PGO.md、DRAPER.md 和老师方案。当前任务是把 DRAPER 的流约束恢复接到
> data/real-pgo-bzip2-v1 中的真实 LBR 数据上。所有新实验随机种子固定为 42。
> 先检查现有代码和数据，再从保存的 bzip2 样本实现最小接入；使用课题组服务器时保持单任务、
> 低优先级，避开他人的进程和目录。

## 研究问题

项目研究采样式 PGO：硬件采样得到的执行信息存在缺失和偏差，DRAPER 在 CFG 上恢复满足流守恒的
边频次，识别可能改变优化决策的位置，并选择少量位置补充插桩计数。最终评价看程序运行时间、
采集开销、计数误差和实际编译决策。

老师方案位于 `references/DRAPER-CCF-A算法模型方案.md`，问题来源位于
`references/插桩式反馈编译优化.md`。

## 已完成的工作

- LLVM C++ 工具可导出早期 IR 的 CFG、循环与调用信息，插入基本块计数器，调用 LLVM profi，
  写回分支权重并读取 BPI 热分支判断。
- Python 求解器实现流守恒、非负约束、Huber 数据项、bootstrap 区间、风险选点和少量插桩后重算。
- Embench 19 个程序和 LLVM BenchmarkGame 7 个程序已形成配对数据；模拟观测使用种子 42。
- 已修复插桩后纯函数内存属性导致计数器更新被优化掉的问题，相关测试覆盖递归调用。
- 已完成 bzip2 1.0.8 的真实 LBR 实验：Intel PMU cycles、LBR、pseudo-probe、llvm-profgen、
  SamplePGO profi、频次传播、插桩 PGO、正确性检查和 9 轮交错计时。
- 真实采样得到 9,434 个样本。LLVM 已读取 profile 并在优化 IR 中写入函数入口次数和分支权重。
- Hydra（OOPSLA 2026）使用的 cBench bzip2e 已在本机编译，前三组原始输入逐字节检查通过。
- Python 测试在迁移前共 21 项通过。

详细结果见 `REAL_HARDWARE_PGO.md`、`RESULTS_SEED42.md` 和 `PAPER_REVIEW.md`。

## 当前真实 LBR 数据

`data/real-pgo-bzip2-v1/` 中保留：

- `perf.data`：实际硬件采样，约 7.4 MiB。
- `perf.script`：带 mmap、任务事件和分支栈的文本，约 60 MiB。
- `baseline/bzip2`：采样时使用的 ELF 二进制，包含 pseudo-probe；必须与 `perf.data` 配套使用。
- `samples.prof`：`llvm-profgen` 生成的 LLVM 文本采样 profile。
- `instrumented.profdata` 与 `instrumented-counts.txt`：相同训练输入的插桩 PGO 参考数据。
- `summary.json` 与 `commands.json`：结果、完整命令和环境记录。
- `source/` 与 `inputs/`：bzip2 1.0.8 源码和 Canterbury 语料。

采集输入是 `bible.txt`；测试输入是 `world192.txt` 和 `E.coli`。当前性能幅度约为 0%–2%，
多数 95% 区间跨过零。它证明真实采样与反馈编译已跑通，扩大应用和独立采集次数后再比较算法收益。

## 下一步：把 DRAPER 接到真实 LBR

按下面顺序做最小实现：

1. **固定恢复对象。** 先使用 bzip2 的 pseudo-probe CFG 与 `samples.prof`，恢复 LLVM SampleProfileLoader
   消费位置上的块和边频次。保存函数、内联上下文、probe 编号和 CFG 边。
2. **增加真实观测读取。** 在 `draper/model.py` 的 `observation_arrays` 前增加一种真实观测格式。
   格式保存观测值、可见位置、事件类型、采样周期和来源；现有 Poisson 格式继续保留。
3. **建立 probe 到 CFG 的对应。** 从匹配的 ELF、pseudo-probe 和 sample profile 提取观测，
   对应到同一次编译产生的 IR。映射置信度不足的位置标为未知。
4. **准备参考计数。** 用相同源码、相同训练输入和指定 IR 阶段的插桩计数评价恢复误差。
   `instrumented.profdata` 可做强基线；若 CFG 阶段不同，重新编译一份与恢复图相同阶段的参考计数。
5. **运行第一组比较。** O3、LLVM profi、DRAPER、插桩 PGO 使用同一训练输入和测试输入；
   先报告计数分布重叠度、流残差和 BPI 热分支变化，再做运行时间比较。
6. **检查真实采样假设。** cycles 样本更偏向耗时位置，LBR 中相邻分支存在相关性。
   第一版将观测权重按事件和代码区间长度记录清楚，后续用多次独立采集估计区间覆盖率。

最先修改的文件通常是 `draper/model.py`、`draper/llvm_bridge.cpp`，再增加一个读取真实 profile 的小脚本。
先用保存的 bzip2 数据完成接入，随后扩展 cBench 多输入。

## Windows 与服务器安排

- Windows ChatGPT 负责阅读项目、控制终端和通过 VPN 连接 `gpu-101`。
- LLVM、ELF、perf 和 LBR 数据处理放在 WSL2 或 Linux 服务器中执行，项目源码可以放在 Windows 目录，
  运行时复制到 WSL 的 Linux 文件系统可获得更稳定的编译和文件访问速度。
- `gpu-101` 之前看到的是虚拟化 CPU，当前可见接口缺少硬件 PMU；它适合编译、求解和数据整理，
  保存的本机 LBR 数据也可以在兼容 LLVM 工具下离线处理。
- 服务器操作保持单任务、低优先级和明确工作目录；perf 只采自己的进程，系统参数保持原值，
  所有文件操作限定在自己的工作目录。
- 新路径和服务器环境会改变命令中的绝对路径。以 `commands.json` 为参考重新生成命令，
  按新路径重建命令。

## 环境与检查

推荐在 WSL2 Ubuntu 或服务器准备：Python 3、NumPy、SciPy、PyYAML、Clang/LLVM 23 开发文件、
CMake/Ninja 或现有的 LLVM 编译配置。Linux 桌面实验使用 Clang/LLVM 23.1.4。

```bash
python3 -m unittest discover -s tests -v
```

如果 Windows 端只需要阅读和继续讨论，先上传 `WINDOWS_HANDOFF.md`、`CONVERSATION.md` 和精简项目包；
要运行真实编译实验时再进入 WSL2 或连接服务器。

## 关键文件

- `DRAPER.md`：算法、数据流、代码结构和运行方法。
- `REAL_HARDWARE_PGO.md`：真实硬件实验、公开基准与存储情况。
- `PAPER_REVIEW.md`：profi、Propeller 等论文对比。
- `DATASET.md`：现有配对数据结构。
- `run_real_pgo.py`：真实 LBR 与 PGO 实验脚本。
- `run_draper.py`：模拟观测和 DRAPER 对比实验入口。
- `draper/model.py`：流约束恢复、区间与选点算法。
- `draper/llvm_bridge.cpp`：LLVM CFG、插桩、profi 与 profile 写回。
- `CONVERSATION.md`：本项目用户与助手对话正文。
