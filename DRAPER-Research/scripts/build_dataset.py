"""构建按程序划分的配对数据：程序图、观测、精确标签与跨阶段候选关系。"""

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

from draper.model import FlowSystem, simulate
from run_draper import ROOT, save


def split_programs(programs, seed=42):
    names = sorted(programs)
    random.Random(seed).shuffle(names)
    held_out = max(1, round(len(names) * 0.15))
    if len(names) < 3:
        raise ValueError("At least three programs are needed for three splits")
    return {name: "test" if index < held_out else "validation" if index < 2 * held_out else "train"
            for index, name in enumerate(names)}


def source_positions(block):
    return {(entry["file"], entry["line"], entry["column"])
            for entry in block.get("source_locations", []) if entry["line"] > 0}


def candidate_mapping(early, optimized):
    """调试位置仅给出候选对应。保留所有候选，后续再结合伪探针和变换语义。"""
    by_position = defaultdict(set)
    for function in early["functions"]:
        for block in function["blocks"]:
            for position in source_positions(block):
                by_position[position].add((function["name"], block["id"]))
    records = []
    for function in optimized["functions"]:
        for block in function["blocks"]:
            shared = defaultdict(int)
            for position in source_positions(block):
                for origin in by_position[position]:
                    shared[origin] += 1
            records.append({"function": function["name"], "block": block["id"],
                            "candidates": [{"function": name, "block": block_id, "shared_positions": count}
                                           for (name, block_id), count in sorted(shared.items())]})
    return {"kind": "candidate correspondence from shared debug locations",
            "early_ir_id": early["ir_id"], "optimized_ir_id": optimized["ir_id"], "blocks": records}


def probe_origins(text):
    function = block = None
    records = []
    for line in text.splitlines():
        match = re.match(r'define\b.*?@("[^"]+"|[^ (]+)\(', line)
        if match:
            function = match.group(1).strip('"')
            block = None
        match = re.match(r"draper\.bb\.(\d+):", line)
        if match:
            block = int(match.group(1))
        match = re.search(r"@llvm\.pseudoprobe\(i64 (-?\d+), i64 (\d+), i32 (\d+)", line)
        if match and function is not None and block is not None:
            records.append({"function": function, "block": block, "guid": int(match.group(1)) % (2 ** 64),
                            "probe_id": int(match.group(2)), "type": int(match.group(3))})
    return records


def make_labels(graph, truth, program):
    records = []
    for function, counts in zip(graph["functions"], truth["functions"]):
        # 支持代码会出现在每个程序里；训练时只使用对应 benchmark 源码中的函数。
        source_file = function.get("source_file", "")
        supervised = f"/src/{program}/" in source_file or Path(source_file).name == f"{program}.c"
        for block in function["blocks"]:
            successors = block["successor_edges"]
            edge_counts = [counts["edges"][index] for index in successors]
            total = sum(edge_counts)
            records.append({"function": function["name"], "block": block["id"], "train_mask": supervised,
                            "count": counts["blocks"][block["id"]],
                            "successor_probabilities": [value / total for value in edge_counts] if total else None,
                            "hot_edges": [value / total > 0.8 for value in edge_counts] if total else None})
    return {"ir_id": graph["ir_id"], "kind": "exact counts and reference hot predicates", "blocks": records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "data/embench-source/embench-iot-embench-2.0rc2")
    parser.add_argument("--suite", choices=("embench", "llvm-game"), default="embench")
    parser.add_argument("--license-file", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "data/dataset-embench-v2")
    parser.add_argument("--programs", nargs="+")
    parser.add_argument("--resume", action="store_true")
    arguments = parser.parse_args()
    source, output = arguments.source.resolve(), arguments.output.resolve()
    available = ([path.name for path in (source / "src").iterdir() if path.is_dir()] if arguments.suite == "embench"
                 else [path.stem for path in source.glob("*.c")])
    programs = sorted(arguments.programs or available)
    if any(Path(name).name != name or name not in available for name in programs):
        raise SystemExit("Unknown benchmark")
    splits = split_programs(programs)
    if output.exists() and not arguments.resume:
        raise SystemExit(f"Refusing to overwrite {output}; use --resume to continue a dataset")
    output.mkdir(parents=True, exist_ok=True)
    config = {"seed": 42, "suite": arguments.suite, "programs": programs, "source": str(source), "stages": ["early", "O1", "O3"],
              "rates": [1 / 16, 1 / 64, 1 / 256], "missing_fraction": 0.15, "splits": splits}
    config_path = output / "configuration.json"
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        raise SystemExit("Dataset configuration changed; use a new output directory")
    save(config_path, config)
    implementation = output / "implementation"
    implementation.mkdir(exist_ok=True)
    for filename in ("build_dataset.py", "run_draper.py", "draper/model.py", "draper/llvm_bridge.cpp"):
        destination = implementation / Path(filename).name
        current = (ROOT / filename).read_bytes()
        if destination.exists() and destination.read_bytes() != current:
            raise SystemExit("Implementation changed; use a new output directory")
        if not destination.exists():
            shutil.copyfile(ROOT / filename, destination)
    if not (output / "source").exists():
        shutil.copytree(source, output / "source")
    if arguments.license_file:
        shutil.copyfile(arguments.license_file, output / "source/LICENSE.TXT")
    summary = {"status": "incomplete", "seed": 42, "source_release": "embench-2.0rc2",
               "source_url": "https://github.com/embench/embench-iot/tree/embench-2.0rc2",
               "input_kind": "upstream built-in input", "observation_kind": "synthetic independent Poisson",
               "programs": [], "failures": []}
    summary["suite"] = arguments.suite
    if arguments.suite == "llvm-game":
        summary["source_release"] = "llvmorg-21.1.0"
        summary["source_url"] = "https://github.com/llvm/llvm-test-suite/tree/llvmorg-21.1.0/SingleSource/Benchmarks/BenchmarkGame"
    rows = []
    bridge = None
    for program in programs:
        if shutil.disk_usage(output).free < 5 * 1024 ** 3:
            raise RuntimeError("Keep at least 5 GiB free while collecting")
        collection = output / "collections" / program
        completed_path = collection / "summary.json"
        complete = completed_path.exists() and json.loads(completed_path.read_text())["status"] == "complete"
        if not complete:
            # 失败记录保存在原目录，重试另建目录，避免覆盖采集日志。
            if collection.exists():
                summary["failures"].append({"program": program, "reason": "incomplete collection; inspect its logs"})
                continue
            print(f"Collecting {program} ({splits[program]})", flush=True)
            command = [sys.executable, ROOT / "run_draper.py", "--source", output / "source", "--output", collection,
                       "--suite", arguments.suite, "--programs", program, "--seed", "42", "--collect-only", "--stages", "O1", "O3"]
            if bridge:
                command.extend(["--bridge", bridge])
            log_path = output / f"{program}.log"
            with log_path.open("w") as log:
                try:
                    result = subprocess.run([str(part) for part in command], stdout=log, stderr=subprocess.STDOUT,
                                            cwd=ROOT, timeout=900)
                    success = result.returncode == 0
                except subprocess.TimeoutExpired:
                    success = False
            if not success:
                summary["failures"].append({"program": program, "log": log_path.name})
                save(output / "summary.json", summary)
                continue
        bridge = collection / "draper-llvm"
        report = json.loads(completed_path.read_text())["programs"][0]
        directory = collection / program
        early = json.loads((directory / "graph.json").read_text())
        save(directory / "probe-origins.json", {"ir_id": early["ir_id"],
                                               "probes": probe_origins((directory / "probes.ll").read_text())})
        for stage in report["stages"]:
            stage_directory = directory / stage["directory"]
            graph = json.loads((stage_directory / "graph.json").read_text())
            truth = json.loads((stage_directory / "exact-0.json").read_text())
            system = FlowSystem(graph)
            save(stage_directory / "labels.json", make_labels(graph, truth, program))
            if stage["stage"] != "early":
                save(stage_directory / "early-candidates.json", candidate_mapping(early, graph))
            for denominator in (16, 64, 256):
                observed = stage_directory / f"observations-1-{denominator}.json"
                save(observed, simulate(system, truth, 1 / denominator, 0.15, seed=42))
                rows.append({"id": f"{program}/{stage['stage']}/1-{denominator}", "program": program,
                             "split": splits[program], "stage": stage["stage"], "seed": 42,
                             "graph": str((stage_directory / "graph.json").relative_to(output)),
                             "observations": str(observed.relative_to(output)),
                             "labels": str((stage_directory / "labels.json").relative_to(output)),
                             "exact_profile": str((stage_directory / "exact-0.json").relative_to(output)),
                             "reference_decisions": str((stage_directory / "reference.decisions.json").relative_to(output)),
                             "reference_remarks": str((stage_directory / "reference.remarks.yaml").relative_to(output))})
        summary["programs"].append({**report, "split": splits[program]})
        save(output / "summary.json", summary)
    with (output / "samples.jsonl").open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    summary["samples"] = len(rows)
    summary["status"] = "complete" if not summary["failures"] else "partial"
    summary["artifact_bytes"] = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    save(output / "summary.json", summary)
    print(f"{summary['status']}: {len(summary['programs'])} programs, {len(rows)} samples; {output}", flush=True)


if __name__ == "__main__":
    main()
