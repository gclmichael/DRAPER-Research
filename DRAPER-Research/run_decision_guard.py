"""最小改动校准与独立试运行选择；选择函数只接收时间，不读取参考计数。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"
import numpy as np
from draper.model import FlowSystem, anchored_profi, evaluate
from analyze_remarks import read_events, compare
from run_draper import ROOT, save

CANDIDATES = ("restored", "calibrated", "restored-anchors")


def select_candidate(samples, minimum_gain=0.01, seed=42):
    """同时比较三个候选，使用 Bonferroni 调整的一侧 bootstrap 下界。"""
    if not 0 <= minimum_gain < 1:
        raise ValueError("Invalid minimum gain")
    arrays = {name: np.asarray(samples[name], dtype=float) for name in ("profi", *CANDIDATES)}
    n = len(arrays["profi"])
    if n < 5 or any(a.shape != (n,) or not np.isfinite(a).all() or (a <= 0).any()
                    for a in arrays.values()):
        raise ValueError("Need at least five paired positive finite times")
    indices = np.random.default_rng(seed).integers(n, size=(10000, n))
    scores = {}
    for name in CANDIDATES:
        ratios = np.log(arrays["profi"] / arrays[name])
        lower = float(np.expm1(np.quantile(ratios[indices].mean(axis=1), 0.05 / len(CANDIDATES))))
        scores[name] = {"speedup": float(np.expm1(ratios.mean())), "lower_bound": lower,
                        "accepted": lower > minimum_gain}
    accepted = [name for name in CANDIDATES if scores[name]["accepted"]]
    selected = max(accepted, key=lambda name: scores[name]["lower_bound"]) if accepted else "profi"
    return {"selected": selected, "minimum_gain": minimum_gain, "scores": scores,
            "rule": "largest one-sided Bonferroni bootstrap lower bound exceeding minimum gain",
            "seed": seed, "rounds": n}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.experiment.resolve(), args.output.resolve()
    original = json.loads((source / "summary.json").read_text())
    if original["status"] != "complete":
        raise ValueError("Source experiment is incomplete")
    output.mkdir(parents=True, exist_ok=False)
    os.nice(10)
    commands = []
    started = time.monotonic()
    def run(argv):
        begin = time.monotonic()
        result = subprocess.run(list(map(str, argv)), cwd=ROOT, capture_output=True, text=True, timeout=1800)
        commands.append({"argv": list(map(str, argv)), "seconds": time.monotonic()-begin,
                         "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        save(output / "commands.json", commands)
        if result.returncode:
            raise RuntimeError(result.stderr[-4000:])
        print(result.stdout, end="", flush=True)
    implementation = output / "implementation"
    implementation.mkdir()
    for name in ("run_decision_guard.py", "measure_runtime.py", "draper/model.py", "analyze_remarks.py"):
        shutil.copyfile(ROOT / name, implementation / Path(name).name)
    records = []
    for entry in original["programs"]:
        name = entry["program"]
        previous, directory = source / name, output / name
        directory.mkdir()
        for file in ("canonical.ll", "graph.json", "profi.ll", "restored.ll", "calibrated.ll"):
            shutil.copyfile(previous / file, directory / file)
        system = FlowSystem(json.loads((previous / "graph.json").read_text()))
        estimate = system.vector(json.loads((previous / "restored.json").read_text()))
        anchors = json.loads((previous / "anchors.json").read_text())["anchors"]
        begin = time.monotonic()
        projected = anchored_profi(system, estimate, anchors)
        elapsed = time.monotonic()-begin
        if any(not np.isclose((system.blocks @ projected)[a["block"]], a["count"], rtol=1e-7, atol=1e-5) for a in anchors):
            raise RuntimeError("Projection lost measured anchors")
        save(directory / "restored-anchors.json", system.profile(projected, "minimum L1 change from restored with measured anchors"))
        run([source / "draper-llvm", "annotate", directory / "canonical.ll", f"--graph={directory / 'graph.json'}",
             f"--profile={directory / 'restored-anchors.json'}", f"--output={directory / 'restored-anchors.ll'}",
             f"--decisions={directory / 'restored-anchors.decisions.json'}"])
        run(["clang", "-O3", directory / "restored-anchors.ll", "-lm", "-fsave-optimization-record",
             f"-foptimization-record-file={directory / 'restored-anchors.remarks.yaml'}", "-o", directory / "restored-anchors"])
        run([directory / "restored-anchors"])
        # Diagnostics do not participate in timing selection.
        events = {v: read_events((directory if v == "restored-anchors" else previous) / f"{v}.remarks.yaml")
                  for v in ("profi", *CANDIDATES)}
        save(directory / "optimization-changes.json", {v: compare(events["profi"], events[v]) for v in CANDIDATES})
        save(directory / "projection-audit.json", {**system.audit(projected), "seconds": elapsed,
             "relative_l1_change_from_restored": float(np.abs(projected-estimate).sum()/max(estimate.sum(), 1))})
        records.append({"program": name})
        print(f"{name}: minimal-change calibration compiled and verified", flush=True)
    save(output / "summary.json", {"status": "complete", "programs": records, "seed": 42,
         "source": str(source), "observation_kind": original["observation_kind"]})
    run([sys.executable, ROOT / "measure_runtime.py", output, "--variants", *CANDIDATES,
         "--rounds", "9", "--seconds", "0.2", "--output", output / "selection-timing"])
    timing = json.loads((output / "selection-timing/summary.json").read_text())
    choices = []
    for record in timing["programs"]:
        choice = {"program": record["program"], **select_candidate(record["seconds_per_execution"])}
        choices.append(choice)
        directory = output / record["program"]
        shutil.copyfile(directory / f"{choice['selected']}.ll", directory / "selected.ll")
    # Freeze choices before independent validation is started.
    save(output / "selection.json", {"status": "frozen", "programs": choices,
         "selection_seconds": time.monotonic()-started, "selection_timing": str(output / "selection-timing")})
    run([sys.executable, ROOT / "measure_runtime.py", output, "--variants", *CANDIDATES, "selected",
         "--rounds", "15", "--seconds", "0.3", "--output", output / "validation-timing"])
    # Exact counts are read only for post-selection evaluation.
    metrics = []
    for choice in choices:
        previous, directory = source / choice["program"], output / choice["program"]
        system = FlowSystem(json.loads((previous / "graph.json").read_text()))
        truth = json.loads((previous / "exact-0.json").read_text())
        profiles = {v: json.loads(((directory if v == "restored-anchors" else previous) / f"{v}.json").read_text())
                    for v in ("profi", *CANDIDATES)}
        metrics.append({"program": choice["program"], "selected": choice["selected"],
                        "metrics": {v: evaluate(system, truth, system.vector(p)) for v, p in profiles.items()}})
    save(output / "evaluation.json", {"status": "complete", "programs": metrics,
         "total_seconds": time.monotonic()-started, "validation": "new timing rounds, same built-in workload"})


if __name__ == "__main__":
    main()
