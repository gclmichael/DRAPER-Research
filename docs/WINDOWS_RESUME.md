# Windows 接续记录 — 2026-11-09

本轮按用户要求读取迁移记录并继续本地工作。node3、node4 仅执行只读环境检查，未上传、安装、编译或部署。
交接文档中的服务器部署建议未作为本轮操作指令执行。新实验种子继续固定为 42；本次解析为确定性操作，不使用随机数。

## E1：恢复与原件

完整包解压于当前文档所在目录，原 ZIP 保留在 `C:/DRAPER-Windows-Transfer-2026-11-09/`。
`state/windows-verification.json` 记录 ZIP 的 SHA256 和逐文件与原包比较的结果。
本轮仅新增文件，原有模型、LLVM bridge、实验数据及报告均保留原内容。
Windows 解压将 Propeller 论文文件名中的冒号改为下划线；校验记录保留这项名称映射，并核对文件内容一致。

## E2：服务器只读快照

下表是本轮 SSH 实际输出，负载和容量仅代表查询时刻。

| 地址 | 实际 hostname | CPU | 逻辑线程 | 内存 | 1 分钟负载 | 根文件系统可用 |
|---|---|---|---:|---|---:|---|
| 10.75.76.4 | node3 | Xeon Platinum 8358P | 128 | 2.0 TiB | 8.23 | 6.0 T |
| 10.75.76.5 | node4 | Xeon Platinum 8358P | 128 | 2.0 TiB | 43.97 | 1.3 T |

使用 cn 账号、BatchMode、StrictHostKeyChecking 和 ClearAllForwardings。
node3 首次默认身份认证失败，显式指定现有 `id_rsa_cn_NODE3` 后连接成功。
node4 经现有 SSH 配置连接成功。私钥内容未读取。
检查命令为 hostname、id、uptime、uname、command -v、free、df、lscpu。
两台当前非交互 shell 的 PATH 都未找到 clang、llvm-config、perf；这不证明其他目录没有安装。
硬件 PMU 可访问性尚未测试。Windows Python 可运行 NumPy/SciPy；WSL 列表为空。

## E3：完成真实 profile 证据读取

新增 `draper/sample_profile.py` 与 `import_real_profile.py`，读取保存的 bzip2 `samples.prof`。
输出 `data/real-lbr-evidence-v1/evidence.json`：

- 14 个顶层函数、148 个内联上下文；162 个上下文均保留 CFGChecksum。
- 1,519 个 body 位置记录，其中 875 个显式零值，9 个调用目标记录。
- 保留父上下文、调用点 probe、discriminator、样本权重、源文件行号。
- 保存事件 `cpu_core/cycles/u`、周期 100000，以及 profile、匹配 ELF、perf.data、summary、commands 的路径和 SHA256。
- 缺失位置与显式零值分开；CFG 映射为 unknown；执行次数采样率为空。

上述位置权重来自 llvm-profgen，并非 1,519 个原始硬件样本；也不把周期 100000 转成执行次数采样率 1/100000。
解析器适配本次保存的嵌套文本格式，对未知记录、重复位置、重复上下文、错误缩进明确报错。
它尚不是可直接传入 `observation_arrays` 的块观测，不产生恢复频次或置信区间。

复现（输出路径必须尚不存在）：

```powershell
python import_real_profile.py --output data/real-lbr-evidence-v2/evidence.json
python -m unittest discover -s tests -v
python state/verify_windows_resume.py
```

## E4：验证与回退

基线：17 项 Python 测试通过，LLVMBridgeTests 类因缺少 LLVM 开发工具跳过。
修改后：19 项 Python 测试通过，LLVMBridgeTests 类同样跳过。
日志分别保存于 `state/windows-baseline-tests.log`、`state/windows-current-tests.log`。
验证脚本逐文件检查原包一致性，独立按源文本核对记录数，重跑真实导入比对输出，检查拒绝覆盖且原输出不变，清理临时产物后核实目录消失。
命令、退出码、stdout/stderr 位于 `state/windows-verification.json`；新增代码补丁位于 `state/real-profile-reader.patch`。
回退本轮代码只需移除补丁列出的三个新增文件；本轮证据目录和接续记录可单独保留或移除，原项目不依赖它们。

## 下一步：恢复前的映射证据

保存的 `.ll` 位于 sample-profi、sample-propagation、instrumented-pgo，属于反馈优化后的 IR。
采样 baseline 目录保存 ELF 和目标文件，但没有进入 SampleProfileLoader 前的 CFG/IR。
`run_real_pgo.py` 也只在反馈版本生成优化后 `.ll`。因此本轮不把优化后块号当作采样 probe 编号。

接下来需要在具备兼容 LLVM 工具的环境，重建或导出与采样编译对应的 probe CFG，逐函数核对 CFGChecksum、内部函数名和内联上下文，再把高置信映射送入流恢复。
原编译使用绝对源码路径和 `-funique-internal-linkage-names`，迁移路径可能改变内部函数标识，必须验证而非直接替换名字。
之后补齐同阶段参考计数，开展计数重叠度、流残差和 BPI 决策比较。
服务器执行仍遵守用户“先不要部署”的要求；本轮没有运行新的服务器实验。
