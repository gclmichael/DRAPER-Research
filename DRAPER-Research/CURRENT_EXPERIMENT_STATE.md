# 当前实验运行状态

本文件是续跑记录；最终统计以各目录的 `summary.json` 为准。

服务器工作目录：`/data/chaoni/chengli/draper-node3`。

## 必须恢复的批次

- `run_public_suites_v2.sh`，进程 1666207，仍等待后续套件。
- PolyBench 的 `run_all_core.py`，进程 **2765991**，被主动暂停以优先完成现代应用。其 Floyd–Warshall 已完成计时，Seidel 已完成核心算法，尚未计时。完成现代应用后，核对进程命令再发送 `SIGCONT`，继续剩余 PolyBench。
- 暂停记录在服务器 `state/polybench-priority-pause.json`。不能忘记恢复此进程。

## 当前现代应用批次

- SQLite、zstd、FFmpeg 均完成普通/插桩构建、验证、全块采集与优化后探针观测。
- **zstd 已全部完成**（含优化后观测、核心算法回放、15 轮独立测试输入交错计时与反例审计），结果在 `data/teacher-app-core-v1/zstd`。
- **SQLite** 完成了优化后观测（87,927 个基本块、174,495 条观测；全部块覆盖率 95.10%，实际执行块覆盖率 68.69%）、3 个补充计数与校准后 20 次重采样；此前在编译回放阶段因 `llc -stop-after=block-placement` 的 `MIR Printing Pass` 耗时 180.2 秒（总耗时 193.6 秒）超过旧的 180 秒子命令超时而中断。
- 已将支持 `--resume` 与 `--command-timeout` 的新版 `run_core_risk.py`、`run_all_core.py`、`evaluate_transformed.py` 同步至 `node3`，并启动后台恢复守护脚本 `state/run-modern-resume-v1.py`（进程 **1048145**，当前子进程 **1048151**）：
  - 直接复用 `sqlite-modern` 已保存的校准计数与 20 次重采样矩阵（`resume-preparation.json` 已生成，已进入 `replay-progress.json` 编译回放阶段），完成后执行 15 轮计时与审计。
  - 等待当前 **FFmpeg** 跟随批次（进程 **4018195** / 子进程 **806306** / **959503**，已完成补充计数并处于校准后 20 次重采样阶段）；若旧内存进程在 `llc` 处触发 180 秒超时，自动以 `--resume --command-timeout 1800` 续跑完成 FFmpeg 的回放、计时与审计，并合并至 `data/teacher-app-core-v1/summary.json`。
  - **在三个现代应用全部变为 `complete` 后，自动校验并向 PolyBench 进程 2765991 发送 `SIGCONT` 恢复执行**。状态记录在 `state/modern-resume-v1-status.json`，日志在 `state/modern-resume-v1.log` 与 `data/teacher-app-core-v1/sqlite-modern-1-resume.log`。
- 公开数据集索引 `data/public-corpus-v1/index.json` 已实际验证全部 70 项输入文件：43 个应用工作负载、40 个应用家族、27 个循环内核；开发/验证/测试为 44/12/14 项。另 Rodinia / MediaBench 子集 10 项已完成全块采集，后台进程 **535531** 等待主套件完成后自动执行。

## 已完成和正在采用的实现

- 原 40 项全块采集、早期计数对照和有效计时全部完成；其中 38 项为旧实现，anagram/Lua 两项为修复后补充。
- PolyBench 27 项普通构建、54 次插桩执行及全块采集均完成。上游仅性能配置排除了 gemm、2mm、3mm。
- 新算法使用优化后观测、候选比例与映射、不可辨识范围、真实内联/机器排列检查，以及满足预算的最小改动比较。
- 新的零计数线性规划消去了零观测和零初值的辅助变量，数学目标不变。20 次 aha 抽样目标值一致；SQLite 单次求解从 3.58 秒降至 1.98 秒，目标值相同，但因存在多个最优解，部分边的分配不同。实际检查位于 `data/sqlite-zero-count-fit-v1`。
- 正在运行的 SQLite 观测仍使用原表达式；其真实实现另存于该观测目录的 `implementation/`。后续进程使用新表达式。
- 计时对字节相同的二进制共用实测序列，记录在 `measured_representative`；每轮进度在 `timing/<program>/progress.json`。
- 决策生成已改为稀疏运算并复用连续存放的重采样矩阵。真实 SQLite 的 300 个高频块、18,145 个决策，旧/新处理时间 58.06/2.18 秒，所有决策与系数一致；aha 全部决策也一致，结果在 `data/sparse-decision-check-v1`。正在执行的 SQLite 核心进程已导入旧实现，后续进程使用新实现。
- 本地 39 项非 LLVM 测试通过；LLVM 桥接代码未变，之前服务器的相关 8 项测试通过。

## 仍需完成

1. 完成现代三项的构建、优化后观测、算法、计时、审计；修复实际失败。
2. 恢复 PolyBench 批次，完成全部 27 项。
3. 主批次随后合并原 40 项全块输入到 `all-flows-v3`，运行 `all-transformed-core-v1`。
4. 重生成并下载 `data/public-results-v1`，更新报告；不得把正在运行或失败项算作完成。
5. SPEC 按用户要求暂缓。老师第 16 节的更广泛系统候选尚未全部覆盖；当前执行第 20 节最小版本的公开套件。
