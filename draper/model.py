"""CFG 频次恢复：稀疏流约束、Huber 拟合、bootstrap 和分支风险。

本模块的求解接口只接收图、观测和 profi 初值。精确计数只在模拟观测、
离线评价，以及明确选中的校准点中使用，避免把答案偷偷传进恢复算法。
"""

import copy
import numpy as np
from scipy import sparse
from scipy.optimize import linprog
from scipy.sparse.linalg import factorized


class FlowSystem:
    def __init__(self, graph, call_constraints=True):
        self.graph = graph
        self.functions = graph["functions"]
        self.edge_offsets = []
        self.block_offsets = []
        edge_count = block_count = 0
        for function in self.functions:
            self.edge_offsets.append(edge_count)
            self.block_offsets.append(block_count)
            edge_count += len(function["edges"])
            block_count += len(function["blocks"])
        incoming = sparse.lil_matrix((block_count, edge_count))
        outgoing = sparse.lil_matrix((block_count, edge_count))
        for function, edge_offset, block_offset in zip(self.functions, self.edge_offsets, self.block_offsets):
            for edge_id, edge in enumerate(function["edges"]):
                if edge["src"] >= 0:
                    outgoing[block_offset + edge["src"], edge_offset + edge_id] += 1
                if edge["dst"] >= 0:
                    incoming[block_offset + edge["dst"], edge_offset + edge_id] += 1
        self.blocks = incoming.tocsr()
        self.cfg = (outgoing - incoming).tocsr()
        calls = []
        # 只约束所有调用均在当前模块内可见、且地址没有逃逸的内部函数。
        # 外部库、函数指针回调和 main 不能套用“入口等于可见调用之和”。
        for callee, entry in zip(self.functions, self.edge_offsets):
            if not call_constraints or not callee["closed_calls"]:
                continue
            row = sparse.lil_matrix((1, edge_count))
            row[0, entry] = 1
            for caller, block_offset in zip(self.functions, self.block_offsets):
                for block in caller["blocks"]:
                    occurrences = block["calls"].count(callee["name"])
                    if occurrences:
                        row -= occurrences * self.blocks.getrow(block_offset + block["id"])
            calls.append(row.tocsr())
        self.calls = sparse.vstack(calls, format="csr") if calls else sparse.csr_matrix((0, edge_count))
        self.constraints = sparse.vstack([self.cfg, self.calls], format="csr")
        self.edge_count = edge_count
        self.block_count = block_count

    def vector(self, profile):
        if profile["ir_id"] != self.graph["ir_id"]:
            raise ValueError("Profile and graph refer to different IR")
        if [entry["name"] for entry in profile["functions"]] != [entry["name"] for entry in self.functions]:
            raise ValueError("Function order differs")
        for function, counts in zip(self.functions, profile["functions"]):
            if len(function["edges"]) != len(counts["edges"]):
                raise ValueError("Edge count differs")
        result = np.concatenate([entry["edges"] for entry in profile["functions"]]).astype(float)
        if not np.isfinite(result).all() or (result < 0).any():
            raise ValueError("Invalid profile counts")
        return result

    def profile(self, edges, kind):
        blocks = self.blocks @ edges
        functions = []
        for function, edge_offset, block_offset in zip(self.functions, self.edge_offsets, self.block_offsets):
            functions.append({"name": function["name"],
                              "edges": edges[edge_offset:edge_offset + len(function["edges"])].tolist(),
                              "blocks": blocks[block_offset:block_offset + len(function["blocks"])].tolist()})
        return {"ir_id": self.graph["ir_id"], "kind": kind, "functions": functions}

    def audit(self, edges):
        return {"cfg_max_absolute_residual": float(np.max(np.abs(self.cfg @ edges), initial=0)),
                "call_max_absolute_residual": float(np.max(np.abs(self.calls @ edges), initial=0)),
                "minimum_count": float(np.min(edges, initial=0))}


def simulate(system, truth, rate=1 / 64, missing=0.15, seed=42):
    if not 0 < rate <= 1 or not 0 <= missing < 1:
        raise ValueError("Invalid sampling rate or missing fraction")
    counts = system.blocks @ system.vector(truth)
    random = np.random.default_rng(seed)
    samples = random.poisson(rate * counts)
    present = random.random(len(counts)) >= missing
    samples[~present] = 0
    functions = []
    for function, offset in zip(system.functions, system.block_offsets):
        stop = offset + len(function["blocks"])
        functions.append({"name": function["name"], "samples": samples[offset:stop].tolist(),
                          "present": present[offset:stop].tolist()})
    return {"ir_id": system.graph["ir_id"], "kind": "synthetic independent Poisson block observations",
            "mapping": "identity at saved early IR, no binary mapping assumed",
            "rate": rate, "missing_fraction": missing, "seed": seed, "functions": functions}


def observation_arrays(system, observations):
    if observations["ir_id"] != system.graph["ir_id"]:
        raise ValueError("Observation/graph mismatch")
    if [entry["name"] for entry in observations["functions"]] != [entry["name"] for entry in system.functions]:
        raise ValueError("Observation function order differs")
    for function, entry in zip(system.functions, observations["functions"]):
        if len(entry["samples"]) != len(function["blocks"]) or len(entry["present"]) != len(entry["samples"]):
            raise ValueError("Observation block count differs")
    samples = np.concatenate([entry["samples"] for entry in observations["functions"]]).astype(float)
    present = np.concatenate([entry["present"] for entry in observations["functions"]]).astype(bool)
    if not np.isfinite(samples).all() or (samples < 0).any() or np.any(samples[~present] != 0):
        raise ValueError("Invalid observed counts")
    rate = float(observations["rate"])
    if not 0 < rate <= 1:
        raise ValueError("Invalid sampling rate")
    return samples, present, rate


def project_flow(target, constraints, right_hand_side):
    """用最小 L1 改动清除数值误差，严格保留非负性和线性等式。"""
    size = len(target)
    identity = sparse.eye(size, format="csr")
    inequalities = sparse.vstack([sparse.hstack([identity, -identity]),
                                  sparse.hstack([-identity, -identity])], format="csr")
    result = linprog(np.r_[np.zeros(size), np.ones(size)], A_ub=inequalities,
                     b_ub=np.r_[target, -target],
                     A_eq=sparse.hstack([constraints, sparse.csr_matrix((constraints.shape[0], size))]),
                     b_eq=right_hand_side, bounds=(0, None), method="highs",
                     options={"primal_feasibility_tolerance": 1e-9, "dual_feasibility_tolerance": 1e-9})
    if not result.success:
        raise RuntimeError(f"Flow projection failed: {result.message}")
    # LP 的浮点容差可能给出 -1e-12 一类结果；裁掉数值负零，调用方再
    # 检查流守恒残差，不能把负计数写进 LLVM 元数据。
    return np.maximum(result.x[:size], 0)


def fit(system, observations, initial, anchors=(), robust=True):
    """固定映射时求解带流约束的稳健二次问题，profi 仅作弱正则和初值。"""
    samples, present, rate = observation_arrays(system, observations)
    if np.shape(initial) != (system.edge_count,) or not np.isfinite(initial).all() or (initial < 0).any():
        raise ValueError("Invalid initial flow")
    scale = max(float(np.max(samples / rate, initial=0)), float(np.max(initial, initial=0)), 1.0)
    operator = system.blocks[present]
    targets = samples[present] / rate / scale
    standard_deviation = np.sqrt(samples[present] + 1) / rate / scale
    weights = 1 / standard_deviation
    normalization = max(float(np.max(weights, initial=0)), 1.0)
    weights /= normalization
    constraints = system.constraints
    right_hand_side = np.zeros(constraints.shape[0])
    for anchor in anchors:
        block_id, count = anchor["block"], anchor["count"]
        if not 0 <= block_id < system.block_count or not np.isfinite(count) or count < 0:
            raise ValueError("Invalid calibration anchor")
        constraints = sparse.vstack([constraints, system.blocks.getrow(block_id)], format="csr")
        right_hand_side = np.r_[right_hand_side, count / scale]
    prior = initial / scale
    estimate = prior.copy()
    robust_weights = np.ones(len(targets))
    identity = sparse.eye(system.edge_count, format="csc")
    regularization = 1e-6
    iterations = 0
    # ADMM 将非负约束与等式约束分开处理；每轮只求解一个稀疏线性系统。
    for outer in range(4 if robust else 1):
        diagonal = weights ** 2 * robust_weights
        hessian = (operator.T @ sparse.diags(diagonal) @ operator + regularization * identity).tocsc()
        linear = np.asarray(operator.T @ (diagonal * targets)).ravel() + regularization * prior
        penalty = max(float(np.median(hessian.diagonal())), 1e-3)
        solve = factorized(hessian + penalty * (identity + constraints.T @ constraints))
        nonnegative = np.maximum(estimate, 0)
        dual_bounds = np.zeros_like(estimate)
        dual_flow = np.zeros(constraints.shape[0])
        for iteration in range(5000):
            estimate = solve(linear + penalty * (nonnegative - dual_bounds)
                             + penalty * constraints.T @ (right_hand_side - dual_flow))
            previous = nonnegative
            nonnegative = np.maximum(estimate + dual_bounds, 0)
            flow_error = constraints @ estimate - right_hand_side
            dual_bounds += estimate - nonnegative
            dual_flow += flow_error
            primal = max(np.max(np.abs(estimate - nonnegative), initial=0),
                         np.max(np.abs(flow_error), initial=0))
            dual = penalty * np.max(np.abs(nonnegative - previous), initial=0)
            if primal < 1e-8 and dual < 1e-8:
                break
            # 热循环与只执行一次的入口可相差百万倍；固定惩罚系数会让
            # 一个残差已很小、另一个仍缓慢变化，因此按残差比例调节。
            if (iteration + 1) % 50 == 0:
                new_penalty = penalty
                if primal > 10 * dual:
                    new_penalty = min(penalty * 2, 1e6)
                elif dual > 10 * primal:
                    new_penalty = max(penalty / 2, 1e-8)
                if new_penalty != penalty:
                    dual_bounds *= penalty / new_penalty
                    dual_flow *= penalty / new_penalty
                    penalty = new_penalty
                    solve = factorized(hessian + penalty * (identity + constraints.T @ constraints))
        iterations += iteration + 1
        if iteration == 4999:
            raise RuntimeError(f"Constrained solver exceeded iteration limit: primal={primal}, dual={dual}, penalty={penalty}")
        residual = (operator @ estimate - targets) / standard_deviation
        robust_weights = np.minimum(1.0, 1.345 / np.maximum(np.abs(residual), 1e-12))
    projected = project_flow(estimate, constraints, right_hand_side)
    result = projected * scale
    audit = system.audit(result)
    if max(audit["cfg_max_absolute_residual"], audit["call_max_absolute_residual"]) > 1e-7 * scale:
        raise RuntimeError("Final flow check failed")
    return result, {**audit, "iterations": iterations,
                    "projection_l1_normalized": float(np.abs(projected - estimate).sum()),
                    "regularization": regularization, "anchors": len(anchors)}


def uncertainty(system, observations, initial, estimate, repetitions=20, seed=42):
    if repetitions < 4:
        raise ValueError("At least four bootstrap repetitions are required")
    _, present, rate = observation_arrays(system, observations)
    random = np.random.default_rng(seed)
    replicates = []
    # 0.5 的平滑项避免零样本位置在所有重采样中永远为零。
    # 这些是经验 bootstrap 区间，需要通过重复实验检查覆盖率。
    expectation = rate * (system.blocks @ estimate) + 0.5
    for repeat in range(repetitions):
        draw = random.poisson(expectation)
        draw[~present] = 0
        resampled = copy.deepcopy(observations)
        for function, offset in zip(resampled["functions"], system.block_offsets):
            function["samples"] = draw[offset:offset + len(function["samples"])].tolist()
        fitted, _ = fit(system, resampled, initial)
        replicates.append(fitted)
    return np.asarray(replicates)


def risks(system, observations, estimate, replicates):
    samples, present, _ = observation_arrays(system, observations)
    block_counts = system.blocks @ estimate
    profile = system.profile(estimate, "flow-constrained Huber estimate")
    decisions = []
    for function, output, edge_offset, block_offset in zip(system.functions, profile["functions"],
                                                          system.edge_offsets, system.block_offsets):
        tags = ["not-a-branch"] * len(function["blocks"])
        for block in function["blocks"]:
            successors = block["successor_edges"]
            if len(successors) < 2:
                continue
            edge_ids = np.array(successors) + edge_offset
            counts = estimate[edge_ids]
            total = counts.sum()
            probabilities = counts / total if total > 0 else np.zeros(len(counts))
            draws = replicates[:, edge_ids]
            totals = draws.sum(axis=1)
            ratios = np.divide(draws, totals[:, None], out=np.zeros_like(draws, dtype=float), where=totals[:, None] > 0)
            lower, upper = np.quantile(ratios, [0.025, 0.975], axis=0)
            global_block = block_offset + block["id"]
            known = bool(present[global_block] and samples[global_block] >= 5 and np.all(totals > 0))
            branch_tag = "stable"
            for slot, edge_id in enumerate(edge_ids):
                expected_hot = bool(probabilities[slot] > 0.8)
                flip = float(np.mean((ratios[:, slot] > 0.8) != expected_hot))
                if not known:
                    tag, interval = "unknown", [0.0, 1.0]
                elif lower[slot] <= 0.8 < upper[slot]:
                    tag, interval = "risky", [float(lower[slot]), float(upper[slot])]
                else:
                    tag, interval = "stable", [float(lower[slot]), float(upper[slot])]
                if tag == "unknown" or (tag == "risky" and branch_tag == "stable"):
                    branch_tag = tag
                decisions.append({"function": function["name"], "block": block["id"],
                                  "global_block": global_block, "slot": slot, "edge": int(edge_id),
                                  "probability": float(probabilities[slot]), "interval": interval,
                                  "hot": expected_hot, "flip_probability": flip, "tag": tag,
                                  "priority": (max(flip, 0.5 if tag == "unknown" else 0.0)
                                               * np.log1p(block_counts[global_block]))})
            tags[block["id"]] = branch_tag
        output["block_tags"] = tags
        output["edge_intervals"] = np.quantile(
            replicates[:, edge_offset:edge_offset + len(function["edges"])], [0.025, 0.975], axis=0).T.tolist()
    return profile, decisions


def select_anchors(system, decisions, budget):
    """按分支风险选择少量计数器位置，只返回位置，不读取精确计数。"""
    selected = []
    for decision in sorted(decisions, key=lambda entry: entry["priority"], reverse=True):
        if decision["tag"] == "stable":
            continue
        function_index = next(index for index, function in enumerate(system.functions) if function["name"] == decision["function"])
        edge = system.functions[function_index]["edges"][decision["edge"] - system.edge_offsets[function_index]]
        # 关键边已拆开，这个基本块计数正好等于待校准的分支边计数。
        block = system.block_offsets[function_index] + edge["counter"]
        if block not in selected and len(selected) < budget:
            selected.append(block)
    return selected


def alternative_anchors(system, estimate, budget, strategy, seed=42):
    """随机和频次对照使用相同的候选块集合，全程只读取恢复次数。"""
    candidates = sorted({block_offset + function["edges"][edge_id]["counter"]
                         for function, block_offset in zip(system.functions, system.block_offsets)
                         for block in function["blocks"] if len(block["successor_edges"]) >= 2
                         for edge_id in block["successor_edges"]})
    if strategy == "random":
        np.random.default_rng(seed).shuffle(candidates)
    elif strategy == "frequency":
        counts = system.blocks @ estimate
        candidates.sort(key=lambda block: (-counts[block], block))
    else:
        raise ValueError(f"Unknown selection strategy: {strategy}")
    return candidates[:budget]


def anchored_profi(system, initial, anchors):
    """在 profi 结果上做最小 L1 改动，同时满足流约束和同一组实测计数。"""
    scale = max(float(np.max(initial, initial=0)), max((anchor["count"] for anchor in anchors), default=0), 1)
    constraints = system.constraints
    right_hand_side = np.zeros(constraints.shape[0])
    for anchor in anchors:
        block, count = anchor["block"], anchor["count"]
        if not 0 <= block < system.block_count or not np.isfinite(count) or count < 0:
            raise ValueError("Invalid measured counter")
        constraints = sparse.vstack([constraints, system.blocks.getrow(block)], format="csr")
        right_hand_side = np.r_[right_hand_side, count / scale]
    result = project_flow(initial / scale, constraints, right_hand_side) * scale
    return result


def overlap(reference, estimate):
    """论文重叠度先分别归一化；两个全零分布视为相同。"""
    reference_total, estimate_total = reference.sum(), estimate.sum()
    if reference_total == 0 or estimate_total == 0:
        return float(reference_total == estimate_total)
    return float(np.minimum(reference / reference_total, estimate / estimate_total).sum())


def evaluate(system, truth, estimate):
    reference = system.vector(truth)
    reference_blocks = system.blocks @ reference
    inferred_blocks = system.blocks @ estimate
    flips = branches = 0
    probability_errors = []
    for function, offset in zip(system.functions, system.edge_offsets):
        for block in function["blocks"]:
            if len(block["successor_edges"]) < 2:
                continue
            indices = np.array(block["successor_edges"]) + offset
            true_counts, inferred = reference[indices], estimate[indices]
            if true_counts.sum() == 0:
                continue
            true_probabilities = true_counts / true_counts.sum()
            probabilities = inferred / inferred.sum() if inferred.sum() > 0 else np.zeros(len(indices))
            probability_errors.extend(np.abs(probabilities - true_probabilities).tolist())
            flips += int(np.sum((probabilities > 0.8) != (true_probabilities > 0.8)))
            branches += len(indices)
    return {**system.audit(estimate),
            "block_relative_l1": float(np.abs(inferred_blocks - reference_blocks).sum() / max(reference_blocks.sum(), 1)),
            "edge_relative_l1": float(np.abs(estimate - reference).sum() / max(reference.sum(), 1)),
            "block_overlap": overlap(reference_blocks, inferred_blocks),
            "edge_overlap": overlap(reference, estimate),
            "positive_block_coverage": float(np.mean(inferred_blocks[reference_blocks > 0] > 0))
            if np.any(reference_blocks > 0) else 1.0,
            "branch_probability_mae": float(np.mean(probability_errors)) if probability_errors else None,
            "hot_predicate_flips": flips, "evaluated_branch_edges": branches}
