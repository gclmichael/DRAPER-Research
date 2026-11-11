"""生成自编的小程序，用已知计算结果检查 LLVM 插桩和 PGO 基础步骤。"""

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time


ROOT = Path(__file__).resolve().parent
CASES = ["diamond", "fixed_loop", "early_exit", "call_contexts", "inline_candidate", "nested_loop"]
OUTPUT = ROOT / "data" / "pilot-v1"
MASK32 = (1 << 32) - 1
MASK64 = (1 << 64) - 1


def reference(case_id, iterations, threshold, seed):
    # 按 C 的 uint32_t/uint64_t 溢出规则计算参考输出，保证跨语言结果可比。
    state = seed
    result = 0
    executed = 0
    taken = 0
    for index in range(iterations):
        state ^= (state << 13) & MASK32
        state ^= state >> 17
        state ^= (state << 5) & MASK32
        mixed = (state * 2654435761) ^ (state >> 7)
        condition = state % 100 < threshold
        executed += 1
        taken += int(condition)
        if case_id == 0:
            result += mixed if condition else state
        elif case_id in (1, 2, 4):
            result += mixed
        elif case_id == 3:
            result += mixed + (1 if condition else 3)
        else:
            for inner in range(threshold):
                value = (state + inner) & MASK32
                result += (value * 2654435761) ^ (value >> 7)
        result &= MASK64
        if case_id == 2 and state % 1000 < threshold:
            break
    semantics = {"source_outer_iterations": executed}
    if case_id in (0, 3, 4):
        semantics["source_condition_true"] = taken
    if case_id == 5:
        semantics["source_inner_iterations"] = executed * threshold
    return str(result) + "\n", semantics


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    tools = {name: shutil.which(name) for name in ("clang", "llvm-profdata")}
    if not all(tools.values()):
        raise SystemExit("clang and llvm-profdata must be on PATH")
    if OUTPUT.exists():
        raise SystemExit(f"Refusing to overwrite {OUTPUT}; archive it or choose a new dataset version")
    if shutil.disk_usage(ROOT).free < 1024 ** 3:
        raise SystemExit("At least 1 GiB free space is required")
    os.nice(10)
    OUTPUT.mkdir(parents=True)
    commands = []
    manifest = {
        "schema": 1,
        "status": "incomplete",
        "kind": "deterministic instrumentation fixtures; not hardware samples",
        "host": platform.platform(),
        "tools": tools,
        "cases": [],
    }

    def run(arguments, environment=None, stderr_path=None):
        command = [str(argument) for argument in arguments]
        record = {"argv": command, "env": environment or {}}
        commands.append(record)
        completed = subprocess.run(
            command, cwd=ROOT, env={**os.environ, "OMP_NUM_THREADS": "1", **(environment or {})},
            capture_output=True, text=True, timeout=30,
        )
        record["returncode"] = completed.returncode
        if stderr_path:
            stderr_path.write_text(completed.stderr)
        elif completed.stderr:
            record["stderr"] = completed.stderr
        if completed.returncode:
            raise RuntimeError(f"Command failed: {command}\n{completed.stderr}")
        return completed.stdout

    started = time.monotonic()
    try:
        manifest["versions"] = {name: run([path, "--version"]) for name, path in tools.items()}
        source = OUTPUT / "profile_cases.c"
        shutil.copyfile(ROOT / "fixtures" / "profile_cases.c", source)
        for case_id, case_name in enumerate(CASES):
            directory = OUTPUT / case_name
            directory.mkdir()
            common = [tools["clang"], "-O3", "-gline-tables-only", f"-DCASE_ID={case_id}"]
            baseline = directory / "baseline"
            instrumented = directory / "instrumented"
            run([*common, source, "-o", baseline])
            run([*common, "-fprofile-generate", "-mllvm", "-print-before-all",
                 "-c", source, "-o", directory / "instrumented.o"],
                stderr_path=directory / "before-pgo-instr-gen.log")
            trace = (directory / "before-pgo-instr-gen.log").read_text()
            marker = "; *** IR Dump Before PGOInstrumentationGen on [module] ***"
            if marker not in trace:
                raise RuntimeError("Requested pre-instrumentation IR trace is missing")
            trace = marker + trace.split(marker, 1)[1].split("; *** IR Dump Before", 1)[0]
            (directory / "before-pgo-instr-gen.log").write_text(trace)
            run([tools["clang"], directory / "instrumented.o", "-fprofile-generate", "-o", instrumented])
            run([*common, "-fprofile-generate", "-S", "-emit-llvm", source,
                 "-o", directory / "instrumented.optimized.ll"])
            run([*common, "-fpseudo-probe-for-profiling", source, "-o", directory / "probe-binary"])
            for input_name, iterations, threshold, seed in (
                ("input-a", 10000, 10, 42), ("input-b", 20000, 90, 42)
            ):
                sample = directory / input_name
                sample.mkdir()
                arguments = [str(iterations), str(threshold), str(seed)]
                expected, semantics = reference(case_id, iterations, threshold, seed)
                (sample / "expected.stdout").write_text(expected)
                for binary in (baseline, directory / "probe-binary"):
                    if run([binary, *arguments]) != expected:
                        raise RuntimeError(f"Incorrect output: {binary}")
                counter_reports = []
                # 两次分别保存和合并，比较的是相同输入的重复计数，不是计数总和。
                for repeat in range(2):
                    raw = sample / f"run-{repeat}.profraw"
                    if run([instrumented, *arguments], {"LLVM_PROFILE_FILE": str(raw)}) != expected:
                        raise RuntimeError("Incorrect instrumented output")
                    merged = sample / f"run-{repeat}.profdata"
                    run([tools["llvm-profdata"], "merge", raw, "-o", merged])
                    report = run([tools["llvm-profdata"], "show", "--all-functions", "--counts", merged])
                    (sample / f"run-{repeat}.counts.txt").write_text(report)
                    counter_reports.append(report)
                if counter_reports[0] != counter_reports[1]:
                    raise RuntimeError("Repeated executions produced different counters")
                pgo = sample / "pgo-binary"
                run([*common, f"-fprofile-use={sample / 'run-0.profdata'}",
                     "-Werror=profile-instr-out-of-date", "-Werror=profile-instr-unprofiled",
                     "-fsave-optimization-record",
                     f"-foptimization-record-file={sample / 'optimization.yaml'}",
                     source, "-o", pgo], stderr_path=sample / "pgo-build.stderr")
                for check_arguments in (["10000", "10", "12345"], ["20000", "90", "67890"]):
                    check_expected, _ = reference(case_id, *map(int, check_arguments))
                    if run([pgo, *check_arguments]) != check_expected:
                        raise RuntimeError("Incorrect PGO output on collection or alternate input")
                manifest["cases"].append({
                    "program": case_name, "case_id": case_id, "input": input_name,
                    "arguments": arguments, "reference_stdout": expected.strip(),
                    "source_semantics_not_ir_labels": semantics,
                    "profile": str((sample / "run-0.profdata").relative_to(OUTPUT)),
                    "repeated_counter_check": "passed", "output_checks": "passed",
                })
            print(f"Prepared {case_name}", flush=True)
        manifest["status"] = "complete"
    finally:
        manifest["preparation_seconds_not_benchmark"] = round(time.monotonic() - started, 3)
        (OUTPUT / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        files = [path for path in OUTPUT.rglob("*") if path.is_file()]
        manifest["files"] = {
            str(path.relative_to(OUTPUT)): {"bytes": path.stat().st_size, "sha256": digest(path)}
            for path in files
        }
        manifest["payload_bytes_excluding_manifest"] = sum(path.stat().st_size for path in files)
        (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Dataset: {OUTPUT}")
    print(f"Payload: {manifest['payload_bytes_excluding_manifest']} bytes")


if __name__ == "__main__":
    main()
