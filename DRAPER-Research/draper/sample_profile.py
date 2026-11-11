"""Read the nested LLVM text sample format used by the saved bzip2 run.

This is a lossless evidence staging layer, not a block-count observation model.
Probe IDs are not block IDs; cycles periods are not execution sampling rates.
Unknown syntax is rejected so evidence cannot silently disappear.
"""

import re


def parse_sample_profile(text):
    contexts = []
    stack = []
    identities = set()
    locations = {}
    for line_number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        if "\t" in raw:
            raise ValueError(f"Line {line_number}: tabs are not supported")
        depth = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()

        def fail(reason):
            raise ValueError(f"Line {line_number}: {reason}: {line}")

        if depth == 0:
            match = re.fullmatch(r"(.+):(\d+):(\d+)", line)
            if not match:
                fail("expected function:total:head")
            name, total, head = match.groups()
            parent = callsite = None
            stack.clear()
        else:
            if depth > len(stack):
                fail("invalid nesting")
            stack = stack[:depth]
            owner = stack[-1]
            checksum = re.fullmatch(r"!CFGChecksum: (\d+)", line)
            if checksum:
                if owner["cfg_checksum"] is not None:
                    fail("duplicate CFG checksum")
                owner["cfg_checksum"] = checksum[1]
                continue
            match = re.fullmatch(r"(\d+)(?:\.(\d+))?: (.+)", line)
            if not match:
                fail("unsupported profile record")
            probe, discriminator, value = match.groups()
            callsite = {"probe_id": int(probe), "discriminator": int(discriminator or 0)}
            if re.match(r"^\d+(?: |$)", value):
                fields = value.split()
                targets = []
                for field in fields[1:]:
                    target = re.fullmatch(r"(.+):(\d+)", field)
                    if not target:
                        fail("invalid call target")
                    targets.append({"function": target[1], "samples": int(target[2])})
                key = (callsite["probe_id"], callsite["discriminator"])
                if key in locations[owner["id"]]:
                    fail("duplicate body location")
                locations[owner["id"]].add(key)
                owner["body"].append({**callsite, "samples": int(fields[0]),
                                      "call_targets": targets, "source_line": line_number})
                continue
            inline = re.fullmatch(r"(.+):(\d+)", value)
            if not inline:
                fail("invalid inline context")
            name, total = inline.groups()
            head = None
            parent = owner["id"]
        identity = (parent, None if callsite is None else tuple(callsite.values()), name)
        if identity in identities:
            fail("duplicate context")
        identities.add(identity)
        context = {"id": len(contexts), "parent_id": parent, "function": name,
                   "callsite": callsite, "total_samples": int(total),
                   "head_samples": None if head is None else int(head),
                   "cfg_checksum": None, "body": [], "source_line": line_number}
        contexts.append(context)
        locations[context["id"]] = set()
        stack.append(context)
    if not contexts:
        raise ValueError("Empty sample profile")
    return contexts


def profile_statistics(contexts):
    records = [record for context in contexts for record in context["body"]]
    return {"top_level_functions": sum(c["parent_id"] is None for c in contexts),
            "inline_contexts": sum(c["parent_id"] is not None for c in contexts),
            "contexts_with_checksum": sum(c["cfg_checksum"] is not None for c in contexts),
            "body_locations": len(records),
            "explicit_zero_locations": sum(r["samples"] == 0 for r in records),
            "call_target_records": sum(len(r["call_targets"]) for r in records)}
