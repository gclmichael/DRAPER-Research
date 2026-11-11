"""交错运行同一程序的各编译版本，保存逐轮时间和配对比较。"""

import argparse
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
import numpy as np
from run_draper import ROOT, save


def paired_improvement(reference, candidate, seed=42):
    """同轮配对，估计时间比的几何平均及 bootstrap 区间。"""
    ratios = np.log(np.asarray(reference) / np.asarray(candidate))
    draws = np.random.default_rng(seed).choice(ratios, size=(2000, len(ratios)), replace=True)
    interval = np.expm1(np.quantile(draws.mean(axis=1), [0.025, 0.975])) * 100
    return {"speedup_percent": float(np.expm1(ratios.mean()) * 100), "ci95_percent": interval.tolist()}


def renamed_main(text):
    if len(re.findall(r"^define\b[^\n]*@main\(", text, re.MULTILINE)) != 1:
        raise ValueError("Expected exactly one defined main function")
    return re.sub(r"@main(?![-a-zA-Z$._0-9])", "@draper_original_main", text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment", type=Path)
    parser.add_argument("--programs", nargs="+")
    parser.add_argument("--variants", nargs="+", help="Measure selected variants plus baseline and profi")
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--seconds", type=float, default=0.15)
    parser.add_argument("--cpu", type=int)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.rounds < 3 or not 0.02 <= arguments.seconds <= 10:
        raise SystemExit("Use at least 3 rounds and 0.02–10 seconds per batch")
    experiment = arguments.experiment.resolve()
    summary = json.loads((experiment / "summary.json").read_text())
    if summary["status"] != "complete":
        raise SystemExit("Finish the experiment before measuring")
    output = (arguments.output or experiment / "timing").resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite {output}")
    output.mkdir(parents=True)
    shutil.copyfile(Path(__file__), output / "measure_runtime.py")
    shutil.copyfile(ROOT / "fixtures/measure_main.c", output / "measure_main.c")
    allowed = sorted(os.sched_getaffinity(0))
    cpu = arguments.cpu if arguments.cpu is not None else allowed[-1]
    if cpu not in allowed:
        raise SystemExit("CPU is outside the allowed affinity")
    os.sched_setaffinity(0, {cpu})
    os.nice(10)
    records = []
    report = {"status": "incomplete", "seed": 42, "cpu": cpu, "rounds": arguments.rounds,
              "target_batch_seconds": arguments.seconds, "cpu_model": Path("/proc/cpuinfo").read_text().split("model name", 1)[-1].splitlines()[0],
              "load_before": os.getloadavg(), "method": "repeated upstream main in one process; paired interleaved rounds",
              "programs": []}

    def run(command, timeout=90):
        command = [str(part) for part in command]
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, "LLVM_PROFILE_FILE": "/dev/null"})
        records.append({"argv": command, "returncode": result.returncode, "stderr": result.stderr})
        if result.returncode:
            raise RuntimeError(f"Failed: {command}\n{result.stderr}")
        return result.stdout

    try:
        for entry in summary["programs"]:
            program = entry["program"]
            if arguments.programs and program not in arguments.programs:
                continue
            source = experiment / program
            directory = output / program
            directory.mkdir()
            variants = {"baseline": source / "canonical.ll"}
            for name in ("reference", "profi", "restored", "calibrated", "random", "frequency", "profi-anchors"):
                if (source / f"{name}.ll").exists():
                    variants[name] = source / f"{name}.ll"
            variants.update({"full-instrumentation": source / "instrumented.ll", "sparse-risk": source / "sparse.ll"})
            for strategy in ("random", "frequency"):
                path = source / f"selection-{strategy}" / "sparse.ll"
                if path.exists():
                    variants[f"sparse-{strategy}"] = path
            if arguments.variants:
                requested = set(arguments.variants) | {"baseline", "profi"}
                if requested - variants.keys():
                    raise ValueError(f"Unknown variants: {requested - variants.keys()}")
                variants = {name: path for name, path in variants.items() if name in requested}
            for name, path in variants.items():
                renamed = directory / f"{name}.ll"
                renamed.write_text(renamed_main(path.read_text()))
                instrumented = name == "full-instrumentation" or name.startswith("sparse-")
                code = renamed
                if instrumented:
                    code = directory / f"{name}.bc"
                    run(["opt", "-passes=instrprof", renamed, "-o", code])
                object_path = directory / f"{name}.o"
                run(["clang", "-O3", "-c", code, "-o", object_path])
                runtime_flags = ["-fprofile-instr-generate"] if instrumented else []
                run(["clang", "-O3", output / "measure_main.c", object_path, "-lm", *runtime_flags, "-o", directory / name])
                run([directory / name, 1])
            # 同一程序的所有版本采用相同循环次数，使每轮处理相同工作量。
            pilot = float(run([directory / "baseline", 10])) / 10
            repetitions = min(1000000, max(1, math.ceil(arguments.seconds / max(pilot, 1e-9))))
            for name in variants:
                run([directory / name, repetitions])
            print(f"Timing {program}: {len(variants)} variants, {repetitions} executions/batch", flush=True)
            samples = {name: [] for name in variants}
            ordering = []
            randomizer = random.Random(42)
            for repeat in range(arguments.rounds):
                names = list(variants)
                randomizer.shuffle(names)
                ordering.append(names)
                for name in names:
                    seconds = float(run([directory / name, repetitions]))
                    if not math.isfinite(seconds) or seconds <= 0:
                        raise RuntimeError("Invalid elapsed time")
                    samples[name].append(seconds / repetitions)
            comparisons = {name: {"vs_baseline": paired_improvement(samples["baseline"], values),
                                  "vs_profi": paired_improvement(samples["profi"], values)}
                           for name, values in samples.items()}
            result = {"program": program, "repetitions_per_batch": repetitions,
                      "seconds_per_execution": samples, "round_order": ordering, "comparisons": comparisons,
                      "binary_bytes": {name: (directory / name).stat().st_size for name in variants}}
            save(directory / "results.json", result)
            report["programs"].append(result)
            save(output / "summary.json", report)
        report["status"] = "complete"
    finally:
        report["load_after"] = os.getloadavg()
        save(output / "summary.json", report)
        save(output / "commands.json", records)
    print(output)


if __name__ == "__main__":
    main()
