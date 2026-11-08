"""检查配对数据的计数、采样复现、程序划分及训练标签。"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

from build_dataset import make_labels, probe_origins
from draper.model import FlowSystem, simulate
from run_draper import save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    arguments = parser.parse_args()
    root = arguments.dataset.resolve()
    summary = json.loads((root / "summary.json").read_text())
    configuration = json.loads((root / "configuration.json").read_text())
    if summary["status"] != "complete" or summary["seed"] != 42:
        raise ValueError("Expected a complete seed-42 dataset")
    rows = [json.loads(line) for line in (root / "samples.jsonl").read_text().splitlines()]
    if len(rows) != len({row["id"] for row in rows}) or len(rows) != summary["samples"]:
        raise ValueError("Sample IDs or count mismatch")
    graphs = {}
    splits = {}
    totals = Counter()
    for row in rows:
        program = row["program"]
        splits.setdefault(program, row["split"])
        if splits[program] != row["split"] or configuration["splits"][program] != row["split"] or row["seed"] != 42:
            raise ValueError("A program crosses dataset splits or seed differs")
        key = row["graph"]
        if key not in graphs:
            graph = json.loads((root / key).read_text())
            truth = json.loads((root / row["exact_profile"]).read_text())
            directory = (root / key).parent
            if truth != json.loads((directory / "exact-1.json").read_text()):
                raise ValueError("Repeated exact measurements differ")
            system = FlowSystem(graph)
            audit = system.audit(system.vector(truth))
            if max(audit["cfg_max_absolute_residual"], audit["call_max_absolute_residual"]) != 0:
                raise ValueError(f"Exact flow inconsistency: {audit}")
            labels = json.loads((root / row["labels"]).read_text())
            if labels != make_labels(graph, truth, program):
                raise ValueError("Reference labels differ from exact profile")
            if not any(label["train_mask"] for label in labels["blocks"]):
                raise ValueError("No benchmark-owned functions selected for learning")
            totals["functions_across_stages"] += len(system.functions)
            totals["blocks_across_stages"] += system.block_count
            totals["edges_across_stages"] += system.edge_count
            totals["benchmark_blocks_across_stages"] += sum(label["train_mask"] for label in labels["blocks"])
            graphs[key] = system, truth
        system, truth = graphs[key]
        observations = json.loads((root / row["observations"]).read_text())
        if observations != simulate(system, truth, observations["rate"], 0.15, seed=42):
            raise ValueError("Observations cannot be reproduced with seed 42")
        for field in ("reference_decisions", "reference_remarks"):
            if not (root / row[field]).is_file():
                raise ValueError(f"Missing artifact: {field}")
    for program in splits:
        directory = root / "collections" / program / program
        origins = json.loads((directory / "probe-origins.json").read_text())["probes"]
        if origins != probe_origins((directory / "probes.ll").read_text()) or not origins:
            raise ValueError("Pseudo-probe origins mismatch")
        totals["pseudo_probe_origins"] += len(origins)
        for stage in ("O1", "O3"):
            mapping = json.loads((directory / stage / "early-candidates.json").read_text())
            for block in mapping["blocks"]:
                totals["optimized_blocks_with_candidates"] += bool(block["candidates"])
                totals["optimized_blocks_with_multiple_candidates"] += len(block["candidates"]) > 1
                totals["optimized_blocks_without_candidates"] += not block["candidates"]
    result = {"status": "passed", "seed": 42, "programs": len(splits), "graphs": len(graphs),
              "samples": len(rows), "programs_per_split": dict(Counter(splits.values())),
              "samples_per_split": dict(Counter(row["split"] for row in rows)), **totals}
    save(root / "validation.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
