"""带硬性修改预算的决策风险投影模块（Decision-Risk Projection & Sparse Calibration Budgeting）。

核心职责：
1. `linear_decisions`：从 CFG 边频次和重采样样本中抽取四类编译决策谓词的局部线性模型：
   - `branch`：分支边概率是否超过 0.8（对应 LLVM `BranchProbabilityInfo::isEdgeHot`）；
   - `block-frequency`：基本块执行频次是否超过所属函数入口频次的 10 倍（研究代理阈值）；
   - `successor-order`：条件分支两个后继块的局部流量大小次序（对应机器基本块布局偏好）；
   - `inline`：调用点内联成本裕量对调用块频次和函数入口频次的最小二乘局部线性敏感度。
2. `bounded_projection`：在严格保持 CFG 流守恒、内部调用一致、非负性及实测校准锚点不变的前提下，
   限制总计数 L1 绝对改变量不超过总频次的 `budget_fraction`（默认 1%），通过线性规划（HiGHS）
   最小化代价加权的决策裕量损失（hinge loss），同时冻结与不稳定决策无关的连通分量并保护稳定决策。
3. `replay_acceptance`：在实际调用 LLVM 重新编译候选 Profile 后，核对真实编译器决策；
   要求所有后验重采样一致的稳定决策不发生翻转，且实际编译决策的预期风险严格下降，否则回退至校准中心解。
4. `budgeted_anchors`：按“预期风险收益 / 预计动态更新次数”排序挑选稀疏校准锚点，
   同时施加每程序（默认 <=3）、每函数（<=2）、每循环（<=1）及总动态更新比例（<=1%）约束。

注意：当前错误代价 `cost` 采用静态指令数与频次的乘积近似，不读取运行计时或参考真值计数。
"""
import numpy as np
from scipy import sparse
from scipy.optimize import linprog


def linear_decisions(system, estimate, draws, inline_records=(), include_stable=False):
    """分支、块热度、后继排序和编译器内联成本的局部线性近似。"""
    result = []
    counts = system.blocks @ estimate
    scale = max(float(estimate.max(initial=0)), 1)
    draw_matrix = np.ascontiguousarray(draws.T)
    def add(kind, name, row, threshold, cost, block, source):
        row=sparse.csr_matrix(row)
        values = np.asarray(row@draw_matrix).ravel() - threshold
        positive = float(np.mean(values > 0))
        if positive in (0, 1) and not include_stable:
            return
        result.append({"kind": kind, "name": name, "row": row, "threshold": threshold,
                       "probability_positive": positive, "desired": positive >= .5,
                       "cost": float(max(cost, 1e-12)), "block": block, "source": source,
                       "safe_margin": float(np.std(values) * .25)})
    for f, eo, bo in zip(system.functions, system.edge_offsets, system.block_offsets):
        entry = system.blocks.getrow(bo)
        for b in f["blocks"]:
            block = bo + b["id"]
            total = system.blocks.getrow(block)
            impact = counts[block] * max(b["instructions"], 1)
            # 显式研究阈值：块执行频次超过函数入口的十倍；不是 LLVM 的全局热度阈值。
            add("block-frequency", f"{f['name']}:{b['id']}", (total-10*entry)/scale, 0,
                impact, block, "configured relative-frequency threshold 10; LLVM frequency replay recorded separately")
            ids = np.asarray(b["successor_edges"], dtype=int) + eo
            if len(ids) < 2 or counts[block] <= 0:
                continue
            denom = max(counts[block], 1)
            for edge in ids:
                row = -.8*total + sparse.csr_matrix(([1.],([0],[edge])),shape=(1,system.edge_count))
                add("branch", str(edge), row/denom, 0, counts[block], block, "LLVM isEdgeHot probability > 0.8")
            # 后继流量排序是布局的局部模型，实际机器块顺序由编译回放单独核对。
            for i, left in enumerate(ids):
                for right in ids[i+1:]:
                    row = sparse.csr_matrix(([1/denom,-1/denom],([0,0],[left,right])),shape=(1,system.edge_count))
                    add("successor-order", f"{left}:{right}", row, 0, counts[block], block,
                        "local edge-flow preference; validate against machine block placement")
    by_name = {f["name"]: (f, eo, bo) for f, eo, bo in zip(system.functions, system.edge_offsets, system.block_offsets)}
    for record in inline_records:
        f, eo, bo = by_name[record["function"]]
        block = bo + record["block"]
        features = np.stack([system.blocks.getrow(block).toarray().ravel(),
                             system.blocks.getrow(bo).toarray().ravel()]) / scale
        design = np.c_[np.ones(len(draws)), draws @ features.T]
        margins = np.asarray(record["margins"], dtype=float)
        coefficients = np.linalg.lstsq(design, margins, rcond=None)[0]
        residual = float(np.sqrt(np.mean((design@coefficients-margins)**2)))
        # 阈值模型拟合差时不根据该近似改计数，记录原始编译器结果供检查。
        if residual > max(float(np.std(margins))*.5, 1):
            continue
        add("inline", record["key"], coefficients[1:] @ features, -coefficients[0],
            counts[block]*record["instructions"], block, "LLVM full inline cost; local least-squares sensitivity")
    return result


def expected_loss(vector, decisions):
    return sum(d["cost"] * ((1-d["probability_positive"]) if d["row"]@vector > d["threshold"]
                             else d["probability_positive"]) for d in decisions)


def replay_acceptance(baseline, posterior, candidate, decisions):
    """检查真实编译结果，并保护所有后验回放一致的已知决策。"""
    stable={key:value for key,value in baseline.items()
            if all(key in draw and draw[key]==value for draw in posterior)}
    changed=[key for key,value in stable.items() if key not in candidate or candidate[key]!=value]
    def loss(actions):
        if any((d['kind'],d['name']) not in actions for d in decisions):return None
        return sum(d['cost']*((1-d['probability_positive']) if actions[(d['kind'],d['name'])]
                              else d['probability_positive']) for d in decisions)
    before,after=loss(baseline),loss(candidate)
    accepted=not changed and before is not None and after is not None and after<before-1e-9*max(before,1)
    return {'accepted':bool(accepted),'expected_loss_before':before,'expected_loss_proposed':after,
            'stable_decisions':len(stable),'changed_or_missing_stable_decisions':changed}


def bounded_projection(system, estimate, decisions, budget_fraction=.01, anchors=(), preserve=()):
    """最小化成本加权的决策裕量损失，限制总绝对改变量，冻结不相关调用连通分量。"""
    if not 0 <= budget_fraction <= 1 or not np.isfinite(estimate).all() or (estimate < 0).any():
        raise ValueError("Invalid risk projection parameters")
    n, m = system.edge_count, len(decisions)
    if not m or budget_fraction == 0:
        return estimate.copy(), {"changed": False, "absolute_change": 0., "budget": 0., "decisions": m}
    scale = max(float(estimate.sum()), 1)
    target = estimate/scale
    costs = np.array([d["cost"] for d in decisions]); costs /= max(costs.sum(), 1e-12)
    active = set()
    for d in decisions:
        active.update(sparse.csr_matrix(d['row']).indices)
    # 通过流守恒/调用等式扩展到相互依赖的边；其余边固定。
    constraints = system.constraints.tocsr()
    while True:
        touched = np.unique(constraints[:, sorted(active)].nonzero()[0]) if active else []
        expanded = active | set(constraints[touched].indices)
        if expanded == active: break
        active = expanded
    eq = constraints
    rhs = np.zeros(eq.shape[0])
    for a in anchors:
        if not 0 <= a["block"] < system.block_count or a["count"] < 0 or not np.isfinite(a["count"]):
            raise ValueError("Invalid measured anchor")
        eq = sparse.vstack([eq, system.blocks.getrow(a["block"])], format="csr")
        rhs = np.r_[rhs, a["count"]/scale]
    eye = sparse.eye(n, format="csr")
    zero = sparse.csr_matrix((n,m))
    upper = [sparse.hstack([eye,-eye,zero]), sparse.hstack([-eye,-eye,zero])]
    bounds_rhs = [target,-target]
    rows=[]; limits=[]
    for d in decisions:
        sign = 1 if d["desired"] else -1
        norm = max(float(abs(sparse.csr_matrix(d['row'])).sum())*scale, abs(d["threshold"]), 1e-12)
        rows.append(-sign*d["row"]*scale/norm)
        limits.append((-sign*d["threshold"]-d["safe_margin"])/norm)
    upper.append(sparse.hstack([sparse.vstack([sparse.csr_matrix(row) for row in rows]),sparse.csr_matrix((m,n)),-sparse.eye(m)]))
    bounds_rhs.append(np.array(limits))
    if preserve:
        fixed=[];fixed_limits=[]
        for d in preserve:
            row=sparse.csr_matrix(d['row']);sign=1 if d['desired'] else -1
            norm=max(float(abs(row).sum())*scale,abs(d['threshold']),1e-12)
            fixed.append(-sign*row*scale/norm)
            fixed_limits.append(-sign*d['threshold']/norm)
        upper.append(sparse.hstack([sparse.vstack(fixed),sparse.csr_matrix((len(fixed),n+m))]))
        bounds_rhs.append(np.array(fixed_limits))
    upper.append(sparse.csr_matrix(np.r_[np.zeros(n),np.ones(n),np.zeros(m)][None,:]))
    bounds_rhs.append(np.array([budget_fraction]))
    variable_bounds=[(0,None) if i in active else (target[i],target[i]) for i in range(n)] + [(0,None)]*(n+m)
    solution=linprog(np.r_[np.zeros(n),np.full(n,1e-6),costs], A_ub=sparse.vstack(upper),
                     b_ub=np.concatenate(bounds_rhs), A_eq=sparse.hstack([eq,sparse.csr_matrix((eq.shape[0],n+m))]),
                     b_eq=rhs,bounds=variable_bounds,method="highs",
                     options={"primal_feasibility_tolerance":1e-9})
    if not solution.success:
        raise RuntimeError(solution.message)
    candidate=np.maximum(solution.x[:n],0)*scale
    change=float(np.abs(candidate-estimate).sum())
    audit=system.audit(candidate)
    if change > budget_fraction*scale+1e-7*scale or max(audit["cfg_max_absolute_residual"],audit["call_max_absolute_residual"]) > 1e-7*scale:
        raise RuntimeError("Projection violates hard constraints")
    before,after=expected_loss(estimate,decisions),expected_loss(candidate,decisions)
    accepted=after < before-1e-9*max(before,1)
    return (candidate if accepted else estimate.copy()), {**audit,"changed":accepted,
        "absolute_change":change if accepted else 0.,"budget":budget_fraction*scale,"decisions":m,
        "active_edges":len(active),"expected_loss_before":before,"expected_loss_candidate":after,
        "expected_loss_after":after if accepted else before,"cost_model":"static instruction/frequency proxy"}


def budgeted_anchors(system, decisions, estimate, total=3, per_function=2, per_loop=1, dynamic_fraction=.01, upper_counts=None):
    """风险收益除以预计更新次数，并限制静态数量、循环区域和动态开销。"""
    counts=system.blocks@estimate
    update_cost=np.maximum(counts,np.asarray(upper_counts)) if upper_counts is not None else counts
    if update_cost.shape!=counts.shape or np.isnan(update_cost).any() or (update_cost<0).any():raise ValueError('Invalid counter cost estimates')
    if total < 0 or per_function < 0 or per_loop < 0 or not 0 <= dynamic_fraction <= 1:
        raise ValueError("Invalid calibration budget")
    budget=max(float(counts.sum())*dynamic_fraction,0)
    chosen=[]; spent=0.; functions={}; loops={}
    if total == 0 or per_function == 0:
        return {"blocks": [], "estimated_dynamic_updates": 0., "dynamic_budget": budget,
                "per_function": per_function, "per_loop": per_loop}
    def position(d):
        block=d["block"]
        fi=max(i for i,offset in enumerate(system.block_offsets) if offset<=block)
        # 分支风险直接测量相应边的计数位置，避免只重复测量分支入口。
        if d.get('kind')=='branch':
            edge=int(d['name'])-system.edge_offsets[fi]
            block=system.block_offsets[fi]+system.functions[fi]['edges'][edge]['counter']
        return block,fi
    for d in sorted(decisions,key=lambda d: d['cost']*min(d['probability_positive'],1-d['probability_positive'])/(update_cost[position(d)[0]]+1),reverse=True):
        block,fi=position(d)
        b=system.functions[fi]["blocks"][block-system.block_offsets[fi]]
        region=(fi,b.get("loop_header",-1))
        in_loop=b.get("loop_depth",0)>0
        if block in chosen or functions.get(fi,0)>=per_function or (in_loop and loops.get(region,0)>=per_loop) or spent+update_cost[block]>budget:
            continue
        chosen.append(block);spent+=update_cost[block];functions[fi]=functions.get(fi,0)+1
        if in_loop:loops[region]=loops.get(region,0)+1
        if len(chosen)>=total:break
    return {"blocks":chosen,"estimated_dynamic_updates":float(spent),"dynamic_budget":budget,
            "per_function":per_function,"per_loop":per_loop,
            "cost_basis":"upper bootstrap count" if upper_counts is not None else "point count"}
