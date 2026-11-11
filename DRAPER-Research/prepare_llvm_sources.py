"""下载 LLVM test-suite 固定版本中的 BenchmarkGame 源码和参考输出。"""

import json
from pathlib import Path
import subprocess

from run_draper import ROOT, save


def main():
    output = ROOT / "data/sources/llvm-test-suite-21.1.0-subset"
    output.mkdir(parents=True, exist_ok=True)
    base = "https://raw.githubusercontent.com/llvm/llvm-test-suite/llvmorg-21.1.0/"
    relative = "SingleSource/Benchmarks/BenchmarkGame"
    listing = output / "upstream-files.json"

    def download(url, destination):
        temporary = destination.with_suffix(destination.suffix + ".partial")
        subprocess.run(["curl", "--http1.1", "--fail", "--location", "--max-time", "40", "--retry", "1",
                        "--silent", "--show-error", url, "--output", str(temporary)], check=True, timeout=90)
        temporary.replace(destination)

    download(f"https://api.github.com/repos/llvm/llvm-test-suite/contents/{relative}?ref=llvmorg-21.1.0", listing)
    files = []
    directory = output / relative
    directory.mkdir(parents=True, exist_ok=True)
    for entry in json.loads(listing.read_text()):
        name = entry["name"]
        if entry["type"] != "file":
            continue
        if Path(name).name != name or entry["size"] > 1024 ** 2:
            raise ValueError("Unexpected source entry")
        destination = directory / name
        url = f"{base}{relative}/{name}"
        download(url, destination)
        if destination.stat().st_size != entry["size"]:
            raise ValueError(f"Source size mismatch: {name}")
        files.append({"path": str(destination.relative_to(output)), "url": url, "bytes": entry["size"]})
    download(base + "LICENSE.TXT", output / "LICENSE.TXT")
    save(output / "source.json", {"repository": "https://github.com/llvm/llvm-test-suite", "release": "llvmorg-21.1.0",
                                 "status": "source and expected output downloaded", "files": files})
    print(f"Downloaded {len(files)} files to {output}")


if __name__ == "__main__":
    main()
