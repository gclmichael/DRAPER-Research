"""使用原版 Embench 输入和检查函数，验证标准 LLVM 插桩式 PGO。"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent
PROGRAMS = ("crc32", "slre", "nettle-aes", "picojpeg", "qrduino", "wikisort")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "data/embench-source/embench-iot-embench-2.0rc2")
    parser.add_argument("--output", type=Path, default=ROOT / "data/embench-v1")
    arguments = parser.parse_args()
    source = arguments.source.resolve()
    output = arguments.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite {output}")
    if not (source / "sconstruct.py").is_file():
        raise SystemExit("Expected an extracted Embench 2.0rc2 source directory")
    tools = {name: shutil.which(name) for name in ("clang", "llvm-profdata", "llvm-readelf", "opt")}
    if not all(tools.values()):
        raise SystemExit("clang, llvm-profdata, llvm-readelf and opt are required")
    if shutil.disk_usage(ROOT).free < 1024 ** 3:
        raise SystemExit("At least 1 GiB of free space is required")
    os.nice(10)
    output.mkdir(parents=True)
    logs = output / "logs"
    logs.mkdir()
    commands = []
    summary = {
        "status": "incomplete",
        "source_directory": str(source),
        "source_release": "embench-2.0rc2",
        "configuration": "upstream native/size board; WARMUP_HEAT=1; GLOBAL_SCALE_FACTOR=1; O3; no LTO",
        "verification": "upstream main calls verify_benchmark; nonzero process exit fails the check",
        "input": "upstream built-in data, identical for profile collection and correctness checks",
        "programs": [],
    }

    def run(command, environment=None):
        command = [str(part) for part in command]
        prefix = logs / f"{len(commands):04d}"
        record = {"argv": command, "env": environment or {}, "log_prefix": str(prefix.relative_to(output))}
        commands.append(record)
        with prefix.with_suffix(".stdout").open("w") as stdout, prefix.with_suffix(".stderr").open("w") as stderr:
            completed = subprocess.run(
                command, cwd=source,
                env={**os.environ, "OMP_NUM_THREADS": "1", **(environment or {})},
                stdout=stdout, stderr=stderr, timeout=45,
            )
        record["returncode"] = completed.returncode
        if completed.returncode:
            raise RuntimeError(f"Command failed: {command}\n{prefix.with_suffix('.stderr').read_text()}")
        return prefix.with_suffix(".stdout").read_text()

    board = source / "examples/native/size"
    # 复用上游构建脚本列出的支持文件；main 会调用 verify_benchmark。
    support = [source / "support/main.c", source / "support/beebsc.c", board / "boardsupport.c"]
    common = [tools["clang"], "-std=gnu17", "-O3", "-gline-tables-only",
              "-DWARMUP_HEAT=1", "-DGLOBAL_SCALE_FACTOR=1", "-I", source / "support", "-I", board]
    try:
        summary["tool_versions"] = {name: run([tool, "--version"]) for name, tool in tools.items()}
        for program in PROGRAMS:
            directory = output / program
            directory.mkdir()
            sources = sorted((source / "src" / program).glob("*.c")) + support
            if len(sources) == len(support):
                raise RuntimeError(f"No C sources for {program}")
            baseline = directory / "baseline"
            instrumented = directory / "instrumented"
            probe = directory / "probe-binary"
            run([*common, *sources, "-lm", "-o", baseline])
            run([baseline])
            run([*common, "-fprofile-generate", *sources, "-lm", "-o", instrumented])
            reports = []
            for repeat in range(2):
                raw = directory / f"run-{repeat}.profraw"
                run([instrumented], {"LLVM_PROFILE_FILE": str(raw)})
                profile = directory / f"run-{repeat}.profdata"
                run([tools["llvm-profdata"], "merge", raw, "-o", profile])
                report = run([tools["llvm-profdata"], "show", "--all-functions", "--counts", profile])
                (directory / f"run-{repeat}.counts.txt").write_text(report)
                reports.append(report)
            if reports[0] != reports[1]:
                raise RuntimeError(f"Repeated counters differ for {program}")
            match = re.search(r"Total functions:\s*(\d+)", reports[0])
            if match is None or int(match.group(1)) == 0:
                raise RuntimeError(f"Empty or unrecognized profile for {program}")
            profile_flags = [f"-fprofile-use={directory / 'run-0.profdata'}",
                             "-Werror=profile-instr-out-of-date", "-Werror=profile-instr-unprofiled"]
            pgo = directory / "pgo-binary"
            run([*common, *profile_flags, *sources, "-lm", "-o", pgo])
            run([pgo])
            run([*common, "-fpseudo-probe-for-profiling", *sources, "-lm", "-o", probe])
            run([probe])
            sections = run([tools["llvm-readelf"], "--sections", probe])
            (directory / "probe-sections.txt").write_text(sections)
            if ".pseudo_probe" not in sections:
                raise RuntimeError(f"No pseudo-probe section for {program}")
            ir_directory = directory / "ir"
            ir_directory.mkdir()
            entry_counts = 0
            branch_weights = 0
            for index, translation_unit in enumerate(sources):
                stem = f"{index:02d}-{translation_unit.stem}"
                instrumented_ir = ir_directory / f"{stem}.instrumented.ll"
                pgo_ir = ir_directory / f"{stem}.pgo.ll"
                run([*common, "-fprofile-generate", "-S", "-emit-llvm", translation_unit, "-o", instrumented_ir])
                run([*common, *profile_flags, "-S", "-emit-llvm", "-fsave-optimization-record",
                     f"-foptimization-record-file={ir_directory / (stem + '.remarks.yaml')}",
                     translation_unit, "-o", pgo_ir])
                for ir in (instrumented_ir, pgo_ir):
                    run([tools["opt"], "-passes=verify", "-disable-output", ir])
                text = pgo_ir.read_text()
                # 检查编译器确实读入了 profile，而不只是编译命令成功退出。
                entry_counts += text.count('!"function_entry_count"')
                branch_weights += text.count('!"branch_weights"')
            if entry_counts == 0 or branch_weights == 0:
                raise RuntimeError(f"Missing consumed profile metadata for {program}")
            summary["programs"].append({
                "name": program, "sources": [str(path.relative_to(source)) for path in sources],
                "profiled_functions": int(match.group(1)),
                "repeat_counts_equal": True, "upstream_correctness_checks": 5,
                "pgo_entry_count_metadata_nodes": entry_counts,
                "pgo_branch_weight_metadata_nodes": branch_weights,
                "verified_ir_files": len(sources) * 2,
            })
            print(f"{program}: checks passed, {match.group(1)} profiled functions", flush=True)
        summary["status"] = "complete"
    finally:
        (output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        summary["artifact_bytes_excluding_summary"] = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Results: {output}")


if __name__ == "__main__":
    main()
