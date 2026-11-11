"""用真实 LBR 样本编译 bzip2，并与普通 O3、插桩 PGO 比较。"""

import argparse
import bz2
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import time

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

from measure_runtime import paired_improvement
from run_draper import ROOT, save


SOURCES = ("blocksort", "huffman", "crctable", "randtable", "compress", "decompress", "bzlib", "bzip2")
INPUTS = ("bible.txt", "world192.txt", "E.coli")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "data/hardware-pgo/bzip2-1.0.8")
    parser.add_argument("--inputs", type=Path, default=ROOT / "data/hardware-pgo/inputs")
    parser.add_argument("--perf", type=Path, default=ROOT / "data/tools/perf-runtime/usr/bin/perf")
    parser.add_argument("--output", type=Path, default=ROOT / "data/real-pgo-bzip2-v1")
    parser.add_argument("--cpu", type=int, default=2)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--period", type=int, default=100000)
    arguments = parser.parse_args()
    output, perf = arguments.output.resolve(), arguments.perf.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite {output}")
    if arguments.cpu not in os.sched_getaffinity(0) or arguments.rounds < 3 or arguments.period < 10000:
        raise SystemExit("Invalid CPU, repetitions or sample period")
    for filename in INPUTS:
        if not (arguments.inputs / filename).is_file():
            raise SystemExit(f"Missing workload: {filename}")
    output.mkdir(parents=True)
    source, inputs = output / "source", output / "inputs"
    shutil.copytree(arguments.source, source)
    inputs.mkdir()
    for name in INPUTS:
        shutil.copyfile(arguments.inputs / name, inputs / name)
    shutil.copyfile(Path(__file__), output / "run_real_pgo.py")
    shutil.copyfile(ROOT / "measure_runtime.py", output / "measure_runtime.py")
    shutil.copyfile(ROOT / "run_draper.py", output / "run_draper.py")
    logs = output / "logs"
    logs.mkdir()
    environment = {**os.environ, "PATH": f"{perf.parent}:{os.environ['PATH']}",
                   "LD_LIBRARY_PATH": f"{perf.parent.parent / 'lib'}:{os.environ.get('LD_LIBRARY_PATH', '')}"}
    os.sched_setaffinity(0, {arguments.cpu})
    os.nice(10)
    commands = []
    summary = {"status": "incomplete", "seed": 42, "program": "bzip2 1.0.8", "cpu": arguments.cpu,
               "source_url": "https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz",
               "input_url": "https://corpus.canterbury.ac.nz/resources/large.tar.gz",
               "train_inputs": ["bible.txt"], "held_out_inputs": ["world192.txt", "E.coli"],
               "observation_kind": "Intel PMU cycles with actual LBR branch stacks",
               "sampling_event": "cpu_core/cycles/u", "sampling_period": arguments.period,
               "perf_event_paranoid": Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip(),
               "load_before": os.getloadavg(), "correctness": [], "performance": []}

    def run(command, extra_environment=None, binary_output=None, timeout=180):
        command = [str(part) for part in command]
        prefix = logs / f"{len(commands):04d}"
        stdout_path = binary_output or prefix.with_suffix(".stdout")
        stderr_path = prefix.with_suffix(".stderr")
        record = {"argv": command, "env": extra_environment or {}, "stdout": str(stdout_path.relative_to(output)),
                  "stderr": str(stderr_path.relative_to(output))}
        commands.append(record)
        started = time.perf_counter()
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            completed = subprocess.run(command, cwd=source, env={**environment, **(extra_environment or {})},
                                       stdout=stdout, stderr=stderr, timeout=timeout)
        record.update(returncode=completed.returncode, elapsed_seconds=time.perf_counter() - started)
        if completed.returncode:
            raise RuntimeError(f"Failed: {command}\n{stderr_path.read_text()[-5000:]}")
        return stdout_path.read_text() if binary_output is None else None

    common = ["-std=gnu17", "-O3", "-g", "-D_FILE_OFFSET_BITS=64"]
    probes = ["-fpseudo-probe-for-profiling", "-fdebug-info-for-profiling", "-funique-internal-linkage-names"]

    def build(name, flags):
        directory = output / name
        directory.mkdir()
        objects = []
        for stem in SOURCES:
            obj = directory / f"{stem}.o"
            run(["clang", *common, *flags, "-fsave-optimization-record",
                 f"-foptimization-record-file={directory / (stem + '.remarks.yaml')}",
                 "-c", source / f"{stem}.c", "-o", obj])
            objects.append(obj)
        binary = directory / "bzip2"
        run(["clang", *common, *flags, *objects, "-Wl,--build-id", "-o", binary])
        return binary

    try:
        summary["compiler"] = run(["clang", "--version"]).splitlines()[0]
        summary["perf"] = run([perf, "version"]).strip()
        print("Building O3 binary with pseudo-probes", flush=True)
        baseline = build("baseline", probes)
        sections = run(["llvm-readelf", "--sections", baseline])
        (output / "baseline-sections.txt").write_text(sections)
        if ".pseudo_probe" not in sections:
            raise RuntimeError("Missing pseudo-probe sections")
        print("Recording real cycles and LBR branches on bible.txt", flush=True)
        run([perf, "record", "-e", summary["sampling_event"], "-c", arguments.period, "-j", "any,u",
             "-o", output / "perf.data", "--", baseline, "-9", "-c", inputs / "bible.txt"],
            binary_output=output / "training.bz2")
        if bz2.decompress((output / "training.bz2").read_bytes()) != (inputs / "bible.txt").read_bytes():
            raise RuntimeError("Sampled run output mismatch")
        run([perf, "script", "--show-mmap-events", "--show-task-events", "-F", "+pid,+brstack",
             "-i", output / "perf.data"], binary_output=output / "perf.script")
        samples = output / "samples.prof"
        run(["llvm-profgen", f"--binary={baseline}", f"--perfdata={output / 'perf.data'}",
             f"--output={samples}", "--format=text"])
        if samples.stat().st_size == 0:
            raise RuntimeError("Empty sample profile")
        (output / "sample-profile-details.txt").write_text(run(["llvm-profdata", "show", "--sample", "--all-functions", samples]))
        # 两个 SamplePGO 版本使用同一份真实样本，只改变流推断选项。
        sampled_flags = {name: [*probes, f"-fprofile-sample-use={samples}", "-mllvm", f"-sample-profile-use-profi={enabled}"]
                         for name, enabled in (("sample-profi", "true"), ("sample-propagation", "false"))}
        binaries = {"baseline": baseline}
        for name, flags in sampled_flags.items():
            print(f"Building {name}", flush=True)
            binaries[name] = build(name, flags)
        instrumented = build("instrumented", ["-fprofile-generate"])
        raw_profile = output / "instrumented.profraw"
        run([instrumented, "-9", "-c", inputs / "bible.txt"], {"LLVM_PROFILE_FILE": str(raw_profile)},
            binary_output=output / "instrumented-training.bz2")
        if bz2.decompress((output / "instrumented-training.bz2").read_bytes()) != (inputs / "bible.txt").read_bytes():
            raise RuntimeError("Instrumented run output mismatch")
        indexed = output / "instrumented.profdata"
        run(["llvm-profdata", "merge", raw_profile, "-o", indexed])
        (output / "instrumented-counts.txt").write_text(run(["llvm-profdata", "show", "--all-functions", "--counts", indexed]))
        instrumented_flags = [f"-fprofile-use={indexed}", "-Werror=profile-instr-out-of-date", "-Werror=profile-instr-unprofiled"]
        binaries["instrumented-pgo"] = build("instrumented-pgo", instrumented_flags)
        summary["profile_metadata"] = {}
        for name, flags in {**sampled_flags, "instrumented-pgo": instrumented_flags}.items():
            counts = {"function_entry_count_nodes": 0, "branch_weight_nodes": 0}
            for stem in SOURCES:
                ir = output / name / f"{stem}.ll"
                run(["clang", *common, *flags, "-S", "-emit-llvm", source / f"{stem}.c", "-o", ir])
                text = ir.read_text()
                counts["function_entry_count_nodes"] += text.count('!"function_entry_count"')
                counts["branch_weight_nodes"] += text.count('!"branch_weights"')
            if not all(counts.values()):
                raise RuntimeError(f"Profile consumption metadata missing for {name}")
            summary["profile_metadata"][name] = counts
        print("Checking upstream vectors and real input round trips", flush=True)
        for name, binary in binaries.items():
            directory = output / name
            for index in (1, 2, 3):
                compressed = directory / f"sample{index}.bz2"
                run([binary, f"-{index}", "-c", source / f"sample{index}.ref"], binary_output=compressed)
                if compressed.read_bytes() != (source / f"sample{index}.bz2").read_bytes():
                    raise RuntimeError(f"Upstream compression vector failed: {name}/{index}")
            for input_name in INPUTS:
                compressed = directory / f"{input_name}.bz2"
                restored = directory / f"{input_name}.restored"
                run([binary, "-9", "-c", inputs / input_name], binary_output=compressed)
                expected = (inputs / input_name).read_bytes()
                if bz2.decompress(compressed.read_bytes()) != expected:
                    raise RuntimeError(f"Independent decompression failed: {name}/{input_name}")
                run([binary, "-d", "-c", compressed], binary_output=restored)
                if restored.read_bytes() != expected:
                    raise RuntimeError(f"Round trip failed: {name}/{input_name}")
                summary["correctness"].append({"variant": name, "input": input_name, "status": "passed"})
        summary["rounds"] = arguments.rounds
        for input_name in INPUTS:
            print(f"Timing compression of {input_name}: {arguments.rounds} paired rounds", flush=True)
            measured = {name: [] for name in binaries}
            orders = []
            randomizer = random.Random(42)
            for repeat in range(arguments.rounds):
                names = list(binaries)
                randomizer.shuffle(names)
                orders.append(names)
                for name in names:
                    started = time.perf_counter()
                    completed = subprocess.run([str(binaries[name]), "-9", "-c", str(inputs / input_name)],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                               env=environment, timeout=120)
                    seconds = time.perf_counter() - started
                    if completed.returncode:
                        raise RuntimeError(completed.stderr.decode())
                    measured[name].append(seconds)
            summary["performance"].append({"input": input_name, "role": "train" if input_name == "bible.txt" else "held-out",
                                           "seconds": measured, "orders": orders,
                                           "vs_baseline": {name: paired_improvement(measured["baseline"], values)
                                                           for name, values in measured.items()},
                                           "profi_vs_propagation": paired_improvement(measured["sample-propagation"], measured["sample-profi"])})
        summary["binary_bytes"] = {name: binary.stat().st_size for name, binary in binaries.items()}
        summary["status"] = "complete"
    finally:
        summary["load_after"] = os.getloadavg()
        save(output / "commands.json", commands)
        save(output / "summary.json", summary)
    print(output)


if __name__ == "__main__":
    main()
