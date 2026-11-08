"""比较 LLVM 实际输出的内联、循环展开和向量化记录。"""

import argparse
from collections import Counter
import json
from pathlib import Path
import yaml


class RemarkLoader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):
    pass


def remark_mapping(loader, tag, node):
    value = loader.construct_mapping(node, deep=True)
    value["remark_type"] = tag
    return value


RemarkLoader.add_multi_constructor("!", remark_mapping)


def read_events(path):
    events = []
    with path.open() as stream:
        for record in yaml.load_all(stream, Loader=RemarkLoader):
            if not record:
                continue
            pass_name = record.get("Pass", "")
            if pass_name not in ("inline", "loop-unroll", "loop-vectorize", "slp-vectorizer"):
                continue
            arguments = {}
            for argument in record.get("Args", []):
                arguments.update({key: value for key, value in argument.items() if key not in ("DebugLoc", "String")})
            location = record.get("DebugLoc", {})
            event = {"pass": pass_name, "name": record.get("Name", ""), "type": record["remark_type"],
                     "function": record.get("Function", ""), "callee": arguments.get("Callee", ""),
                     "file": location.get("File", ""), "line": location.get("Line", 0),
                     "column": location.get("Column", 0), "cost": arguments.get("Cost"),
                     "threshold": arguments.get("Threshold")}
            try:
                event["threshold_minus_cost"] = float(event["threshold"]) - float(event["cost"])
            except (ValueError, TypeError):
                event["threshold_minus_cost"] = None
            events.append(event)
    return events


def successful_events(events, pass_name):
    # 同一源码位置可能出现多个内联上下文，使用多重集合保留出现次数。
    # 这里比较成功变换事件，不把“记录缺失”直接当成一次相反决策。
    return Counter((event["function"], event["callee"], event["file"], event["line"],
                    event["column"], event["name"])
                   for event in events if event["pass"] == pass_name and event["type"] == "Passed"
                   and event["file"] and event["line"])


def compare(reference, candidate):
    results = {}
    for pass_name in ("inline", "loop-unroll", "loop-vectorize", "slp-vectorizer"):
        expected = successful_events(reference, pass_name)
        actual = successful_events(candidate, pass_name)
        def details(counter, events):
            entries = []
            for key, count in counter.items():
                matching = [event for event in events if event["pass"] == pass_name and
                            (event["function"], event["callee"], event["file"], event["line"], event["column"], event["name"]) == key]
                entries.append({"occurrences": count, "events_at_location": matching})
            return entries

        results[pass_name] = {"reference_events": sum(expected.values()), "candidate_events": sum(actual.values()),
                              "reference_only": sum((expected - actual).values()),
                              "candidate_only": sum((actual - expected).values()),
                              "reference_only_details": details(expected - actual, reference),
                              "candidate_only_details": details(actual - expected, candidate)}
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment", type=Path)
    arguments = parser.parse_args()
    experiment = arguments.experiment.resolve()
    summary = json.loads((experiment / "summary.json").read_text())
    if summary["status"] != "complete":
        raise SystemExit("Experiment must finish before comparing remarks")
    output = experiment / "remarks-comparison.json"
    if output.exists():
        raise SystemExit(f"Refusing to overwrite {output}")
    programs = []
    for program in summary["programs"]:
        directory = experiment / program["program"]
        reference = read_events(directory / "reference.remarks.yaml")
        variants = {}
        for name in ("profi", "restored", "risk-gated", "uniform-conservative", "calibrated", "random", "frequency", "profi-anchors"):
            if not (directory / f"{name}.remarks.yaml").exists():
                continue
            events = read_events(directory / f"{name}.remarks.yaml")
            variants[name] = {"comparison": compare(reference, events), "events": events}
        programs.append({"program": program["program"], "reference_events": reference, "variants": variants})
    output.write_text(json.dumps({"kind": "LLVM optimization remark event comparison", "programs": programs}, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
