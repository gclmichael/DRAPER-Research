"""Stage saved real LBR-derived evidence without inventing a probe-to-CFG map."""

import argparse
import hashlib
import json
from pathlib import Path

from draper.sample_profile import parse_sample_profile, profile_statistics


def fingerprint(path):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path.resolve()), "sha256": digest, "bytes": path.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/real-pgo-bzip2-v1"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.input
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    contexts = parse_sample_profile((root / "samples.prof").read_text(encoding="utf-8"))
    event, period = summary["sampling_event"], summary["sampling_period"]
    if not isinstance(event, str) or not event or type(period) is not int or period <= 0:
        raise ValueError("Invalid sampling event or period")
    evidence = {
        "schema": "draper.real-profile-evidence.v1",
        "kind": "llvm-profgen nested pseudo-probe text profile",
        "sampling_event": event, "sampling_period": period,
        "value_unit": "llvm-profgen sample weight; not exact execution count",
        "execution_sampling_rate": None,
        "mapping_status": "unknown: matching pre-SampleProfileLoader CFG required",
        "missing_location_policy": "unknown; explicit zero records remain observed zeros",
        "uncertainty_model": "unestimated; correlated LBR and cycles bias retained",
        "provenance": {name: fingerprint(root / name) for name in (
            "samples.prof", "baseline/bzip2", "perf.data", "summary.json", "commands.json")},
        "statistics": profile_statistics(contexts), "contexts": contexts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(evidence, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(evidence["statistics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
