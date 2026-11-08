"""下载 Hydra 使用的 cBench bzip2 子集，在本机编译并检查压缩结果。"""

import argparse
import bz2
import json
import os
from pathlib import Path
import shlex
import subprocess

from run_draper import ROOT, save
from run_real_pgo import SOURCES


def parse_inputs(text):
    """只提取输入路径和重复次数，避免执行上游文件中的 shell 重定向。"""
    sections = text.strip().split("=====")
    records = []
    for section in sections[1:]:
        number, command, repetitions = section.strip().splitlines()
        arguments = shlex.split(command)
        path = arguments[arguments.index("-c") + 1]
        if not path.startswith("../../"):
            raise ValueError(f"Unexpected input path: {path}")
        relative = Path(path.removeprefix("../../"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe input path: {path}")
        records.append({"id": int(number), "path": relative.as_posix(),
                        "upstream_repetitions": int(repetitions)})
    if len(records) != int(sections[0].strip()):
        raise ValueError("Input count mismatch")
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "data/cbench-bzip2-hydra-v1")
    parser.add_argument("--inputs", type=int, default=3, choices=range(1, 21))
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.nice(10)
    base = "https://raw.githubusercontent.com/lac-dcc/hydra/main/Benchmark/cBench/"
    summary = {"status": "incomplete", "repository": "https://github.com/lac-dcc/hydra",
               "paper_doi": "10.1145/3798247", "seed": 42, "files": [], "checks": [],
               "wrapper_repetitions": 1}

    def download(relative):
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".partial")
        subprocess.run(["curl", "--http1.1", "--fail", "--location", "--silent", "--show-error",
                        "--max-time", "90", "--retry", "1", base + relative, "-o", str(temporary)],
                       check=True, timeout=200)
        temporary.replace(destination)
        summary["files"].append({"path": relative, "url": base + relative,
                                 "bytes": destination.stat().st_size})
        return destination

    try:
        source = output / "bzip2e/src"
        for filename in [*(stem + ".c" for stem in SOURCES), "loop-wrap.c", "bzlib.h", "bzlib_private.h",
                         "_ccc_info_datasets", "Makefile.llvm"]:
            download("bzip2e/src/" + filename)
        download("bzip2e/LICENSE")
        inputs = parse_inputs((source / "_ccc_info_datasets").read_text())[:arguments.inputs]
        for record in inputs:
            download(record["path"])
        # 保留原版 main 包装函数；首次检查每个输入压缩一次，缩短本机运行时间。
        (source / "_finfo_dataset").write_text("1\n")
        binary = output / "bzip2"
        command = ["clang", "-std=gnu89", "-O3", "-g", *[str(source / (stem + ".c")) for stem in SOURCES],
                   str(source / "loop-wrap.c"), "-o", str(binary)]
        summary["compile_command"] = command
        summary["compiler"] = subprocess.check_output(["clang", "--version"], text=True).splitlines()[0]
        with (output / "compile.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=log, check=True, timeout=180)
        for record in inputs:
            original = output / record["path"]
            compressed = output / f"input-{record['id']}.bz2"
            with compressed.open("wb") as stdout:
                subprocess.run([str(binary), "-z", "-k", "-f", "-c", str(original)], cwd=source,
                               stdout=stdout, stderr=subprocess.PIPE, check=True, timeout=180)
            # 独立实现解压并逐字节比较，也能发现包装程序吞掉错误退出码的问题。
            if bz2.decompress(compressed.read_bytes()) != original.read_bytes():
                raise ValueError(f"Compression mismatch: {original}")
            summary["checks"].append({**record, "input_bytes": original.stat().st_size, "status": "passed"})
        summary["status"] = "complete"
    finally:
        save(output / "summary.json", summary)
    print(json.dumps(summary["checks"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
