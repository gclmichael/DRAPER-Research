"""CFG 频次恢复核心求解器：稀疏流守恒约束、变换观测 L1/Huber 拟合、重采样不确定性区间与分支风险分析。

核心职责：
1. `FlowSystem`：从导出的控制流图（CFG）与调用图构建稀疏流守恒矩阵（流入 = 流出）及模块内封闭调用一致约束；
2. `observation_operator`：将早期基本块观测或经编译变换（TEG）组合后的观测矩阵转换为边变量上的线性算子；
3. `fit_linear` / `fit`：在严格满足流守恒、调用一致、非负性及实测锚点等式约束下，
   求解方差加权的稀疏线性规划（自动消去零观测与零初值辅助变量以提升大规模图求解效率）；
4. `uncertainty` / `risks`：通过参数化泊松重采样估计边与基本块频次的 95% 置信区间，识别跨越 0.8 阈值的风险分支；
5. `select_anchors` / `anchored_profi`：按分支风险选取校准锚点，并在流守恒下做最小 L1 改动投影；
6. `evaluate`：离线对比恢复计数与真值计数的 L1 相对误差、分布重叠度（overlap）及热分支谓词翻转数。

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
        """将原始 Profile 字典转换为 LP 求解器所需的稀疏计数向量。
        检查图 ID 与函数顺序一致性，确保数据有效性。"""
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
        """将优化的计数向量反序列化回 Profile 格式。
        计算基本块计数以完整表达程序运行特征。"""
        blocks = self.blocks @ edges
        functions = []
        for function, edge_offset, block_offset in zip(self.functions, self.edge_offsets, self.block_offsets):
            functions.append({"name": function["name"],
                              "edges": edges[edge_offset:edge_offset + len(function["edges"])].tolist(),
                              "blocks": blocks[block_offset:block_offset + len(function["blocks"])].tolist()})
        return {"ir_id": self.graph["ir_id"], "kind": kind, "functions": functions}

    def audit(self, edges):
        """计算残差与指标，用于检查流守恒和调用图约束满足度。"""
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
    if 'operator_rows' in observations:
        samples=np.asarray(observations['samples'],dtype=float)
        present=np.asarray(observations['present'],dtype=bool)
        rate=float(observations['rate'])
        if samples.ndim!=1 or samples.shape!=present.shape or len(samples)!=len(observations['operator_rows']):
            raise ValueError('Transformed observation dimensions differ')
        if not np.isfinite(samples).all() or (samples<0).any() or np.any(samples[~present]!=0) or not 0<rate<=1:
            raise ValueError('Invalid transformed observations')
        return samples,present,rate
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


def observation_operator(system,observations):
    """每个观测是早期基本块次数的已声明线性组合，再转成边变量。"""
    if 'operator_rows' not in observations:return system.blocks
    rows=[];columns=[];values=[]
    for i,row in enumerate(observations['operator_rows']):
        if not row:raise ValueError('Unknown correspondence must be omitted, not assigned zero')
        for term in row:
            block=term['block'];coefficient=float(term['coefficient'])
            if not isinstance(block,int) or not 0<=block<system.block_count or not np.isfinite(coefficient) or coefficient<0:
                raise ValueError('Invalid transformation coefficient')
            rows.append(i);columns.append(block);values.append(coefficient)
    matrix=sparse.csr_matrix((values,(rows,columns)),shape=(len(observations['operator_rows']),system.block_count))
    if np.any(np.asarray(matrix.sum(axis=1)).ravel()==0):raise ValueError('Empty transformation row')
    return matrix@system.blocks


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


def fit_linear(system,observations,initial,anchors=()):
    """带观测方差权重的绝对偏差目标，直接求解稀疏线性规划。"""
    samples,present,rate=observation_arrays(system,observations)
    if np.shape(initial)!=(system.edge_count,) or not np.isfinite(initial).all() or (initial<0).any():raise ValueError('Invalid initial flow')
    scale=max(float(np.max(samples/rate,initial=0)),float(np.max(initial,initial=0)),1.)
    matrix=observation_operator(system,observations)[present];target=samples[present]/rate/scale
    weights=1/np.sqrt(samples[present]+1);weights/=max(float(weights.max(initial=0)),1e-12)
    # 系数和频次均非负，零目标的绝对偏差恰好是预测值，可直接加入目标而无需辅助变量。
    positive=target>0;positive_prior=initial>0;n=system.edge_count
    linear=np.asarray(matrix[~positive].T@weights[~positive]).ravel()+1e-6*(~positive_prior)
    eliminated_zero_observations=int((~positive).sum())
    matrix=matrix[positive];target=target[positive];weights=weights[positive]
    m=len(target);k=int(positive_prior.sum());eye=sparse.eye(n,format='csr')[positive_prior]
    unit=sparse.eye(m,format='csr');prior_unit=sparse.eye(k,format='csr')
    zero=sparse.csr_matrix((m,k));empty=sparse.csr_matrix((k,m))
    upper=sparse.vstack([sparse.hstack([matrix,-unit,zero]),sparse.hstack([-matrix,-unit,zero]),
                         sparse.hstack([eye,empty,-prior_unit]),sparse.hstack([-eye,empty,-prior_unit])],format='csr')
    eq=system.constraints;rhs=np.zeros(eq.shape[0])
    for anchor in anchors:
        b=anchor['block'];c=anchor['count']
        if not 0<=b<system.block_count or not np.isfinite(c) or c<0:raise ValueError('Invalid anchor')
        eq=sparse.vstack([eq,system.blocks.getrow(b)],format='csr');rhs=np.r_[rhs,c/scale]
    problem={'c':np.r_[linear,weights,np.full(k,1e-6)],'A_ub':upper,
         'b_ub':np.r_[target,-target,initial[positive_prior]/scale,-initial[positive_prior]/scale],
         'A_eq':sparse.hstack([eq,sparse.csr_matrix((eq.shape[0],m+k))]),'b_eq':rhs,'bounds':(0,None)}
    options={'primal_feasibility_tolerance':1e-9,'dual_feasibility_tolerance':1e-9}
    result=linprog(**problem,method='highs',options=options);retry_reason=None
    if not result.success:
        # 热冷频次差距很大时，预处理偶尔把可行问题判为数值不确定；原约束和容差保持相同。
        retry_reason=result.message
        result=linprog(**problem,method='highs-ds',options={**options,'presolve':False})
    if not result.success:raise RuntimeError(result.message)
    solution=np.maximum(result.x[:n],0)*scale;audit=system.audit(solution)
    anchor_error=max((abs(float((system.blocks.getrow(v['block'])@solution).item())-v['count']) for v in anchors),default=0.)
    numerical_correction=0.
    if max(audit['cfg_max_absolute_residual'],audit['call_max_absolute_residual'],anchor_error)>1e-6:
        # 单位归一化后的负零被裁掉时，热循环中可能放大成可见残差；用较温和的尺度修复同一组约束。
        repair_scale=max(float(solution.max(initial=0))/1e6,1.)
        repaired=project_flow(solution/repair_scale,eq,rhs*scale/repair_scale)*repair_scale
        numerical_correction=float(np.abs(repaired-solution).sum());solution=repaired;audit=system.audit(solution)
    anchor_error=max((abs(float((system.blocks.getrow(v['block'])@solution).item())-v['count']) for v in anchors),default=0.)
    feasibility_tolerance=max(1e-6,1e-12*scale)
    if max(audit['cfg_max_absolute_residual'],audit['call_max_absolute_residual'],anchor_error)>feasibility_tolerance:
        raise RuntimeError('Flow or measured anchor verification failed')
    return solution,{**audit,'objective':'variance-weighted absolute deviation with L1 prior','iterations':result.nit,
                    'eliminated_zero_observations':eliminated_zero_observations,'eliminated_zero_prior_variables':n-k,
                    'anchors':len(anchors),'regularization':1e-6,'retry_reason':retry_reason,
                    'numerical_correction_l1':numerical_correction,'feasibility_tolerance':feasibility_tolerance}


def fit(system, observations, initial, anchors=(), robust=True, method='linear'):
    """固定映射时拟合频次；默认绝对偏差，也保留原有 Huber 迭代供比较。"""
    if method=='linear':return fit_linear(system,observations,initial,anchors)
    if method!='huber':raise ValueError('Unknown fitting method')
    samples, present, rate = observation_arrays(system, observations)
    if np.shape(initial) != (system.edge_count,) or not np.isfinite(initial).all() or (initial < 0).any():
        raise ValueError("Invalid initial flow")
    scale = max(float(np.max(samples / rate, initial=0)), float(np.max(initial, initial=0)), 1.0)
    operator = observation_operator(system,observations)[present]
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
        for iteration in range(20000):
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
        if iteration == 19999:
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


def uncertainty(system, observations, initial, estimate, repetitions=20, seed=42, anchors=()):
    if repetitions < 4:
        raise ValueError("At least four bootstrap repetitions are required")
    _, present, rate = observation_arrays(system, observations)
    random = np.random.default_rng(seed)
    replicates = []
    # 0.5 的平滑项避免零样本位置在所有重采样中永远为零。
    # 这些是经验 bootstrap 区间，需要通过重复实验检查覆盖率。
    expectation = rate * (observation_operator(system,observations) @ estimate) + 0.5
    for repeat in range(repetitions):
        draw = random.poisson(expectation)
        draw[~present] = 0
        resampled = copy.deepcopy(observations)
        if 'operator_rows' in resampled:resampled['samples']=draw.tolist()
        else:
            for function, offset in zip(resampled["functions"], system.block_offsets):
                function["samples"] = draw[offset:offset + len(function["samples"])].tolist()
        fitted, _ = fit(system, resampled, initial, anchors=anchors)
        replicates.append(fitted)
    return np.asarray(replicates)


def risks(system, observations, estimate, replicates):
    samples, present, _ = observation_arrays(system, observations)
    if 'operator_rows' in observations:
        # 一个优化后计数可能来自多个早期块；仅把唯一来源的计数作为直接支持。
        block_samples=np.zeros(system.block_count);block_present=np.zeros(system.block_count,dtype=bool)
        for row,value,available in zip(observations['operator_rows'],samples,present):
            if available and len(row)==1:
                block=row[0]['block'];block_samples[block]+=value;block_present[block]=True
        samples,present=block_samples,block_present
    block_counts = system.blocks @ estimate
    profile = system.profile(estimate, "flow-constrained frequency estimate")
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
    scale = max(max(float(np.max(initial, initial=0)), max((anchor["count"] for anchor in anchors), default=0)) / 1e6, 1)
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
