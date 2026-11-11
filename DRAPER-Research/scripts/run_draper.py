"""在 Embench 上运行 DRAPER 原型，保留每一步的输入、输出和编译命令。"""

import argparse
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time

# 稀疏求解和编译均顺序运行，避免数学库自动启动大量线程。
for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

import numpy as np
from draper.model import FlowSystem, alternative_anchors, anchored_profi, evaluate, fit, risks, select_anchors, simulate, uncertainty


ROOT = Path(__file__).resolve().parent


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def check_reference_output(actual, expected):
    """LLVM 参考文件含退出码；文字逐项比较，浮点结果保留明确的数值容差。"""
    expected = expected.removesuffix("exit 0\n").split()
    actual = actual.split()
    if len(actual) != len(expected):
        raise RuntimeError("Output token count differs from upstream reference")
    for observed, reference in zip(actual, expected):
        if observed == reference:
            continue
        if re.fullmatch(r"[+-]?\d+", reference):
            raise RuntimeError(f"Integer output differs: {observed} versus reference {reference}")
        try:
            equal = math.isclose(float(observed), float(reference), rel_tol=1e-7, abs_tol=1e-8)
        except ValueError:
            equal = False
        if not equal:
            raise RuntimeError(f"Output differs: {observed} versus reference {reference}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "data/embench-source/embench-iot-embench-2.0rc2")
    parser.add_argument("--suite", choices=("embench", "llvm-game"), default="embench")
    parser.add_argument("--output", type=Path, default=ROOT / "data/draper-v1")
    parser.add_argument("--programs", nargs="+", default=["crc32", "slre", "qrduino"])
    parser.add_argument("--rate", type=float, default=1 / 64)
    parser.add_argument("--missing", type=float, default=0.15)
    parser.add_argument("--bootstrap", type=int, default=20)
    parser.add_argument("--anchors", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--collect-only", action="store_true", help="Collect paired IR and exact profiles")
    parser.add_argument("--stages", nargs="*", choices=("O1", "O3"), default=[])
    parser.add_argument("--bridge", type=Path, help="Reuse an already compiled LLVM helper")
    parser.add_argument("--selection-controls", action="store_true")
    arguments = parser.parse_args()
    source, output = arguments.source.resolve(), arguments.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite {output}")
    if not 0 < arguments.rate <= 1 or not 0 <= arguments.missing < 1 or arguments.bootstrap < 4 or arguments.anchors < 0:
        raise SystemExit("Invalid experiment parameters")
    if any(Path(program).name != program or
           not ((source / "src" / program).is_dir() if arguments.suite == "embench" else (source / f"{program}.c").is_file())
           for program in arguments.programs):
        raise SystemExit("Unknown benchmark name")
    if shutil.disk_usage(ROOT).free < 1024 ** 3:
        raise SystemExit("At least 1 GiB free space is required")
    tool_names = ("clang", "clang++", "llvm-config", "llvm-link", "opt", "llvm-profdata", "llvm-readelf", "llvm-objdump")
    tools = {name: shutil.which(name) for name in tool_names}
    if not all(tools.values()):
        raise SystemExit("LLVM compiler, development libraries and command-line tools are required")
    os.nice(10)
    output.mkdir(parents=True)
    logs = output / "logs"
    logs.mkdir()
    implementation = output / "implementation"
    implementation.mkdir()
    for filename in ("run_draper.py", "draper/model.py", "draper/llvm_bridge.cpp"):
        shutil.copyfile(ROOT / filename, implementation / Path(filename).name)
    commands = []
    expected_output = None
    summary = {"status": "incomplete", "observation_kind": "synthetic Poisson samples, not PMU records",
               "rate": arguments.rate, "missing": arguments.missing, "seed": arguments.seed,
               "bootstrap_repetitions": arguments.bootstrap, "programs": []}
    summary["suite"] = arguments.suite

    def run(command, environment=None, timeout=90):
        command = [str(part) for part in command]
        prefix = logs / f"{len(commands):04d}"
        record = {"argv": command, "env": environment or {}, "log": str(prefix.relative_to(output))}
        commands.append(record)
        with prefix.with_suffix(".stdout").open("w") as stdout, prefix.with_suffix(".stderr").open("w") as stderr:
            completed = subprocess.run(command, cwd=ROOT, env={**os.environ, **(environment or {})},
                                       stdout=stdout, stderr=stderr, timeout=timeout)
        record["returncode"] = completed.returncode
        if completed.returncode:
            raise RuntimeError(f"Failed: {command}\n{prefix.with_suffix('.stderr').read_text()[-5000:]}")
        stdout_text = prefix.with_suffix(".stdout").read_text()
        if expected_output is not None and Path(command[0]).is_relative_to(directory):
            check_reference_output(stdout_text, expected_output)
        return stdout_text

    try:
        bridge = output / "draper-llvm"
        if arguments.bridge:
            shutil.copyfile(arguments.bridge.resolve(), bridge)
            bridge.chmod(0o755)
        else:
            flags = shlex.split(run([tools["llvm-config"], "--cxxflags", "--ldflags", "--libs",
                                     "core", "passes", "analysis", "profiledata", "transformutils", "irreader"]))
            run([tools["clang++"], implementation / "llvm_bridge.cpp", *flags, "-O1", "-o", bridge])
        summary["llvm_version"] = run([tools["llvm-config"], "--version"]).strip()
        summary["numpy_version"] = np.__version__
        board = source / "examples/native/size"
        for program in arguments.programs:
            print(f"{program}: extracting CFG and collecting exact counters", flush=True)
            directory = output / program
            directory.mkdir()
            if arguments.suite == "embench":
                sources = sorted((source / "src" / program).glob("*.c")) + [
                    source / "support/main.c", source / "support/beebsc.c", board / "boardsupport.c"]
            else:
                sources = [source / f"{program}.c"]
                expected_output = (source / f"{program}.reference_output").read_text()
                (directory / "expected.stdout").write_text(expected_output)
            bitcode = []
            for index, translation_unit in enumerate(sources):
                destination = directory / f"{index:02d}-{translation_unit.stem}.bc"
                run([tools["clang"], "-std=gnu17", "-O1", "-Xclang", "-disable-llvm-passes",
                     "-gline-tables-only", "-DWARMUP_HEAT=1", "-DGLOBAL_SCALE_FACTOR=1",
                     "-I", source / "support", "-I", board, "-emit-llvm", "-c", translation_unit,
                     "-o", destination])
                bitcode.append(destination)
            linked = directory / "linked.bc"
            early = directory / "early.ll"
            canonical = directory / "canonical.ll"
            instrumented = directory / "instrumented.ll"
            graph_path = directory / "graph.json"
            run([tools["llvm-link"], *bitcode, "-o", linked])
            # LLVM 23 的单次 InstCombine 可能还留下下一轮可优化的指令。
            # 这里固定做一轮，不要求已无进一步优化机会；后面仍检查 IR 合法性。
            run([tools["opt"], "-passes=function(mem2reg,simplifycfg,instcombine<no-verify-fixpoint>)",
                 "-S", linked, "-o", early])
            run([bridge, "prepare", early, f"--output={canonical}", f"--graph={graph_path}",
                 f"--instrumented={instrumented}"])
            run([tools["opt"], "-passes=instrprof", instrumented, "-o", directory / "lowered.bc"])
            run([tools["clang"], "-O3", "-c", directory / "lowered.bc", "-o", directory / "instrumented.o"])
            run([tools["clang"], directory / "instrumented.o", "-fprofile-instr-generate", "-lm",
                 "-o", directory / "instrumented"])
            truths = []
            for repeat in range(2):
                raw = directory / f"exact-{repeat}.profraw"
                indexed = directory / f"exact-{repeat}.profdata"
                exact = directory / f"exact-{repeat}.json"
                run([directory / "instrumented"], {"LLVM_PROFILE_FILE": str(raw)})
                run([tools["llvm-profdata"], "merge", raw, "-o", indexed])
                run([bridge, "truth", canonical, f"--graph={graph_path}", f"--profile={indexed}", f"--output={exact}"])
                truths.append(json.loads(exact.read_text()))
            if truths[0] != truths[1]:
                raise RuntimeError(f"Exact counters are not deterministic: {program}")
            graph = json.loads(graph_path.read_text())
            system = FlowSystem(graph)
            truth = truths[0]
            reference = system.vector(truth)
            reference_audit = system.audit(reference)
            if max(reference_audit["cfg_max_absolute_residual"], reference_audit["call_max_absolute_residual"]) != 0:
                raise RuntimeError(f"Exact counter/CFG mapping failed: {reference_audit}")
            if arguments.collect_only:
                run([tools["opt"], "-passes=pseudo-probe", "-S", canonical, "-o", directory / "probes.ll"])
                run([tools["clang"], "-O3", directory / "probes.ll", "-lm", "-o", directory / "probe-binary"])
                run([directory / "probe-binary"])
                sections = run([tools["llvm-readelf"], "--sections", "--symbols", directory / "probe-binary"])
                if ".pseudo_probe" not in sections:
                    raise RuntimeError("Pseudo-probe section missing")
                (directory / "probe-sections.txt").write_text(sections)
                (directory / "probe-disassembly.txt").write_text(
                    run([tools["llvm-objdump"], "-d", "--no-show-raw-insn", "--line-numbers", directory / "probe-binary"]))
                stages = [{"stage": "early", "directory": ".", "functions": len(system.functions),
                           "blocks": system.block_count, "edges": system.edge_count, "audit": reference_audit}]
                # 每个优化阶段重新采集自己的精确次数，保留展开和合并后的真实执行情况。
                for stage in arguments.stages:
                    stage_directory = directory / stage
                    stage_directory.mkdir()
                    optimized = stage_directory / "optimized.ll"
                    stage_graph = stage_directory / "graph.json"
                    stage_ir = stage_directory / "canonical.ll"
                    run([tools["opt"], f"-passes=default<{stage}>", "-S", canonical, "-o", optimized,
                         f"-pass-remarks-output={stage_directory / 'transformations.yaml'}"])
                    run([bridge, "prepare", optimized, f"--output={stage_ir}", f"--graph={stage_graph}",
                         f"--instrumented={stage_directory / 'instrumented.ll'}"])
                    run([tools["opt"], "-passes=instrprof", stage_directory / "instrumented.ll",
                         "-o", stage_directory / "lowered.bc"])
                    run([tools["clang"], "-O3", "-c", stage_directory / "lowered.bc", "-o", stage_directory / "instrumented.o"])
                    run([tools["clang"], stage_directory / "instrumented.o", "-fprofile-instr-generate", "-lm",
                         "-o", stage_directory / "instrumented"])
                    stage_truths = []
                    for repeat in range(2):
                        raw = stage_directory / f"exact-{repeat}.profraw"
                        indexed = stage_directory / f"exact-{repeat}.profdata"
                        exact = stage_directory / f"exact-{repeat}.json"
                        run([stage_directory / "instrumented"], {"LLVM_PROFILE_FILE": str(raw)})
                        run([tools["llvm-profdata"], "merge", raw, "-o", indexed])
                        run([bridge, "truth", stage_ir, f"--graph={stage_graph}", f"--profile={indexed}", f"--output={exact}"])
                        stage_truths.append(json.loads(exact.read_text()))
                    if stage_truths[0] != stage_truths[1]:
                        raise RuntimeError(f"Repeated stage counters differ: {program}/{stage}")
                    stage_system = FlowSystem(json.loads(stage_graph.read_text()))
                    audit = stage_system.audit(stage_system.vector(stage_truths[0]))
                    if max(audit["cfg_max_absolute_residual"], audit["call_max_absolute_residual"]) != 0:
                        raise RuntimeError(f"Stage flow mismatch: {audit}")
                    stages.append({"stage": stage, "directory": stage, "functions": len(stage_system.functions),
                                   "blocks": stage_system.block_count, "edges": stage_system.edge_count, "audit": audit})
                for entry in stages:
                    stage_directory = directory / entry["directory"]
                    stage_ir = stage_directory / "canonical.ll"
                    run([bridge, "annotate", stage_ir, f"--graph={stage_directory / 'graph.json'}",
                         f"--profile={stage_directory / 'exact-0.json'}", f"--output={stage_directory / 'reference.ll'}",
                         f"--decisions={stage_directory / 'reference.decisions.json'}"])
                    run([tools["clang"], "-O3", stage_directory / "reference.ll", "-lm", "-fsave-optimization-record",
                         f"-foptimization-record-file={stage_directory / 'reference.remarks.yaml'}", "-o", stage_directory / "reference"])
                    run([stage_directory / "reference"])
                report = {"program": program, "stages": stages, "correctness": "all collected variants passed upstream verification"}
                save(directory / "report.json", report)
                summary["programs"].append(report)
                save(output / "summary.json", summary)
                print(f"{program}: collected {len(stages)} stages", flush=True)
                continue
            observations = simulate(system, truth, arguments.rate, arguments.missing, arguments.seed)
            observation_path = directory / "observations.json"
            save(observation_path, observations)
            profi_path = directory / "profi.json"
            run([bridge, "profi", canonical, f"--graph={graph_path}", f"--profile={observation_path}", f"--output={profi_path}"])
            profi_profile = json.loads(profi_path.read_text())
            initial = system.vector(profi_profile)
            print(f"{program}: fitting {system.edge_count} edges, {arguments.bootstrap} bootstrap repetitions", flush=True)
            fit_started = time.perf_counter()
            estimate, solver_audit = fit(system, observations, initial)
            solver_audit["seconds"] = time.perf_counter() - fit_started
            replicates = uncertainty(system, observations, initial, estimate, arguments.bootstrap, arguments.seed)
            restored, decisions = risks(system, observations, estimate, replicates)
            save(directory / "restored.json", restored)
            save(directory / "risk.json", decisions)
            np.savez_compressed(directory / "bootstrap.npz", edges=replicates)
            # 校准点先由风险选择，再实际编译、运行稀疏计数版本。
            # 选点和求解都不读取全量参考计数；参考值只用于后面的检查。
            selected = select_anchors(system, decisions, arguments.anchors)
            selection_path = directory / "selected-blocks.json"
            save(selection_path, {"ir_id": graph["ir_id"], "blocks": selected})
            anchors_path = directory / "anchors.json"
            if selected:
                sparse_ir = directory / "sparse.ll"
                run([bridge, "calibrate", canonical, f"--graph={graph_path}", f"--selection={selection_path}",
                     f"--output={sparse_ir}"])
                run([tools["opt"], "-passes=instrprof", sparse_ir, "-o", directory / "sparse.bc"])
                run([tools["clang"], "-O3", "-c", directory / "sparse.bc", "-o", directory / "sparse.o"])
                run([tools["clang"], directory / "sparse.o", "-fprofile-instr-generate", "-lm", "-o", directory / "sparse"])
                run([directory / "sparse"], {"LLVM_PROFILE_FILE": str(directory / "sparse.profraw")})
                run([tools["llvm-profdata"], "merge", directory / "sparse.profraw", "-o", directory / "sparse.profdata"])
                run([bridge, "read-anchors", canonical, f"--graph={graph_path}", f"--selection={selection_path}",
                     f"--profile={directory / 'sparse.profdata'}", f"--output={anchors_path}"])
            else:
                save(anchors_path, {"kind": "no calibration positions selected", "anchors": []})
            anchors = json.loads(anchors_path.read_text())["anchors"]
            true_blocks = system.blocks @ reference
            if any(anchor["count"] != true_blocks[anchor["block"]] for anchor in anchors):
                raise RuntimeError("Sparse measurements differ from deterministic full instrumentation")
            calibrated, calibration_audit = fit(system, observations, initial, anchors=anchors)
            save(directory / "calibrated.json", system.profile(calibrated, "flow fit with sparse measured anchors"))
            variants = {"reference": directory / "exact-0.json", "profi": profi_path,
                        "restored": directory / "restored.json", "risk-gated": directory / "restored.json",
                        "uniform-conservative": directory / "restored.json",
                        "calibrated": directory / "calibrated.json"}
            controls = {}
            if arguments.selection_controls:
                matched_profi = anchored_profi(system, initial, anchors)
                matched_path = directory / "profi-anchors.json"
                save(matched_path, system.profile(matched_profi, "profi plus minimum L1 projection with identical measured anchors"))
                variants["profi-anchors"] = matched_path
                controls["profi-anchors"] = {"selected_blocks": selected,
                                             "dynamic_increments": sum(anchor["count"] for anchor in anchors),
                                             "metrics": evaluate(system, truth, matched_profi)}
                # 对照与风险选点使用相同数量的计数器；各位置都实际插桩执行。
                for strategy in ("random", "frequency"):
                    positions = alternative_anchors(system, estimate, len(selected), strategy, arguments.seed)
                    control_directory = directory / f"selection-{strategy}"
                    control_directory.mkdir()
                    selection = control_directory / "selected-blocks.json"
                    save(selection, {"ir_id": graph["ir_id"], "blocks": positions})
                    run([bridge, "calibrate", canonical, f"--graph={graph_path}", f"--selection={selection}",
                         f"--output={control_directory / 'sparse.ll'}"])
                    run([tools["opt"], "-passes=instrprof", control_directory / "sparse.ll", "-o", control_directory / "sparse.bc"])
                    run([tools["clang"], "-O3", "-c", control_directory / "sparse.bc", "-o", control_directory / "sparse.o"])
                    run([tools["clang"], control_directory / "sparse.o", "-fprofile-instr-generate", "-lm", "-o", control_directory / "sparse"])
                    run([control_directory / "sparse"], {"LLVM_PROFILE_FILE": str(control_directory / "sparse.profraw")})
                    run([tools["llvm-profdata"], "merge", control_directory / "sparse.profraw", "-o", control_directory / "sparse.profdata"])
                    run([bridge, "read-anchors", canonical, f"--graph={graph_path}", f"--selection={selection}",
                         f"--profile={control_directory / 'sparse.profdata'}", f"--output={control_directory / 'anchors.json'}"])
                    measured = json.loads((control_directory / "anchors.json").read_text())["anchors"]
                    if any(anchor["count"] != true_blocks[anchor["block"]] for anchor in measured):
                        raise RuntimeError("Control measurements differ from full instrumentation")
                    fitted, audit = fit(system, observations, initial, anchors=measured)
                    destination = directory / f"{strategy}.json"
                    save(destination, system.profile(fitted, f"flow fit with {strategy} measured anchors"))
                    variants[strategy] = destination
                    controls[strategy] = {"selected_blocks": positions, "dynamic_increments": sum(anchor["count"] for anchor in measured),
                                          "solver": audit, "metrics": evaluate(system, truth, fitted)}
            run([tools["clang"], "-O3", canonical, "-lm", "-o", directory / "baseline"])
            run([directory / "baseline"])
            for variant, profile_path in variants.items():
                annotated = directory / f"{variant}.ll"
                extra = ["--risk-gate"] if variant == "risk-gated" else []
                if variant == "uniform-conservative":
                    extra = ["--drop-branch-weights"]
                run([bridge, "annotate", canonical, f"--graph={graph_path}", f"--profile={profile_path}",
                     f"--output={annotated}", f"--decisions={directory / (variant + '.decisions.json')}", *extra])
                run([tools["clang"], "-O3", annotated, "-lm", "-fsave-optimization-record",
                     f"-foptimization-record-file={directory / (variant + '.remarks.yaml')}",
                     "-o", directory / variant])
                run([directory / variant])
            reference_decisions = json.loads((directory / "reference.decisions.json").read_text())["decisions"]
            function_counts = {entry["name"]: entry["blocks"] for entry in truth["functions"]}
            executed = [function_counts[entry["function"]][int(entry["block"].rsplit(".", 1)[1])] > 0
                        for entry in reference_decisions]
            replay = {}
            for variant in variants:
                actual = json.loads((directory / f"{variant}.decisions.json").read_text())["decisions"]
                if len(actual) != len(reference_decisions):
                    raise RuntimeError("Decision record count differs")
                flips = sum(entry["hot"] != reference_entry["hot"] for entry, reference_entry in zip(actual, reference_decisions))
                active_flips = sum(active and entry["hot"] != reference_entry["hot"]
                                   for active, entry, reference_entry in zip(executed, actual, reference_decisions))
                replay[variant] = {"LLVM_hot_decision_disagreements": flips, "branch_edges": len(actual),
                                   "executed_edge_disagreements": active_flips, "executed_branch_edges": sum(executed),
                                   "branches_with_weights": sum(entry["has_profile_weights"] for entry in actual)}
            lower, upper = np.quantile(replicates, [0.025, 0.975], axis=0)
            report = {"program": program, "functions": len(system.functions), "blocks": system.block_count,
                      "edges": system.edge_count, "call_constraints": system.calls.shape[0],
                      "calibration_counters": len(anchors),
                      "calibration_dynamic_increments": sum(anchor["count"] for anchor in anchors),
                      "full_dynamic_increments": float(true_blocks.sum()),
                      "exact_audit": reference_audit, "solver": solver_audit, "calibration_solver": calibration_audit,
                      "profi": evaluate(system, truth, initial), "restored": evaluate(system, truth, estimate),
                      "calibrated": evaluate(system, truth, calibrated),
                      "selection_controls": controls,
                      "empirical_edge_interval_coverage_this_run": float(np.mean((lower <= reference) & (reference <= upper))),
                      "risk_tags": {tag: sum(entry["tag"] == tag for entry in decisions) for tag in ("stable", "risky", "unknown")},
                      "decision_replay": replay, "correctness": "all executable variants passed upstream verification"}
            save(directory / "report.json", report)
            summary["programs"].append(report)
            print(f"{program}: verified all binaries; block relative L1 profi={report['profi']['block_relative_l1']:.4f}, "
                  f"restored={report['restored']['block_relative_l1']:.4f}", flush=True)
        summary["status"] = "complete"
    finally:
        save(output / "commands.json", commands)
        save(output / "summary.json", summary)
    print(f"Results: {output}")


if __name__ == "__main__":
    main()
