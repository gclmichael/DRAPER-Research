"""用更新后的插桩器复查已保存实验的精确计数和稀疏计数。"""

import argparse
import json
import os
from pathlib import Path
import subprocess

from run_draper import save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment", type=Path)
    parser.add_argument("--bridge", type=Path, required=True)
    arguments = parser.parse_args()
    experiment = arguments.experiment.resolve()
    bridge = arguments.bridge.resolve()
    output = experiment / "instrumentation-recheck"
    output.mkdir()
    os.nice(10)
    commands = []

    def run(command, environment=None):
        command = [str(part) for part in command]
        result = subprocess.run(command, capture_output=True, text=True,
                                env={**os.environ, **(environment or {})}, timeout=90)
        commands.append({"argv": command, "returncode": result.returncode, "stderr": result.stderr})
        if result.returncode:
            raise RuntimeError(result.stderr)

    checked = []
    try:
        for entry in json.loads((experiment / "summary.json").read_text())["programs"]:
            program = entry["program"]
            original = experiment / program
            directory = output / program
            directory.mkdir()
            run([bridge, "prepare", original / "early.ll", f"--output={directory / 'canonical.ll'}",
                 f"--graph={directory / 'graph.json'}", f"--instrumented={directory / 'full.ll'}"])
            if (original / "canonical.ll").read_bytes() != (directory / "canonical.ll").read_bytes():
                raise RuntimeError("Canonical IR changed during recheck")
            for name, source_directory in (("full", original), ("risk", original),
                                           ("random", original / "selection-random"),
                                           ("frequency", original / "selection-frequency")):
                if name != "full":
                    run([bridge, "calibrate", original / "canonical.ll", f"--graph={original / 'graph.json'}",
                         f"--selection={source_directory / 'selected-blocks.json'}", f"--output={directory / (name + '.ll')}"])
                run(["opt", "-passes=instrprof", directory / f"{name}.ll", "-o", directory / f"{name}.bc"])
                run(["clang", "-O3", "-c", directory / f"{name}.bc", "-o", directory / f"{name}.o"])
                run(["clang", directory / f"{name}.o", "-fprofile-instr-generate", "-lm", "-o", directory / name])
                run([directory / name], {"LLVM_PROFILE_FILE": str(directory / f"{name}.profraw")})
                run(["llvm-profdata", "merge", directory / f"{name}.profraw", "-o", directory / f"{name}.profdata"])
                action = "truth" if name == "full" else "read-anchors"
                selection = [] if name == "full" else [f"--selection={source_directory / 'selected-blocks.json'}"]
                run([bridge, action, original / "canonical.ll", f"--graph={original / 'graph.json'}",
                     f"--profile={directory / (name + '.profdata')}", f"--output={directory / (name + '.json')}", *selection])
                expected = original / "exact-0.json" if name == "full" else source_directory / "anchors.json"
                if json.loads((directory / f"{name}.json").read_text()) != json.loads(expected.read_text()):
                    raise RuntimeError(f"Counts changed for {program}/{name}")
            checked.append(program)
        save(output / "summary.json", {"status": "passed", "programs": checked, "counter_sets_per_program": 4})
    finally:
        save(output / "commands.json", commands)
    print(f"Rechecked {len(checked)} programs, all full and selected counts match")


if __name__ == "__main__":
    main()
