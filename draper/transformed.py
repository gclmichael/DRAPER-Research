"""变换证据图（TEG）观测系数矩阵、离散映射候选推断与不确定性区间估计模块。

核心职责：
1. `relation` / `exclusive_merges`：将优化前早期 IR 基本块与优化后二进制探针之间的四类编译变换
   （`inline` 内联分摊、`clone` 副本分摊、`merge-exclusive` 二路互斥分支汇合相加、`loop` 循环展开）
   编码为稀疏观测矩阵行系数 $A_{iu}$。
2. `infer`：对多个离散映射与副本比例候选（scenarios）分别在流守恒约束下做 L1 拟合，
   结合候选先验与泊松对数似然（plug-in Poisson likelihood）计算各映射候选的后验权重并加权合成频次估计。
3. `propagate_bounds` / `ambiguity_bounds`：在保持可见观测与校准锚点不变的条件下，
   通过非负线性等式约束传播或逐块 LP 求解，计算不可直接辨识基本块的最小/最大可行频次范围。
4. `intervals`：结合映射候选重采样、泊松观测重采样与不可辨识范围（ambiguity bounds），
   输出边与基本块频次的 95% 置信区间及不可辨识块列表。
5. `coverage` / `calibrate_width` / `widen`：离线评估区间覆盖率，并在独立校准集上学习区间宽度扩张系数。
"""
import copy
from collections import deque
import numpy as np
from scipy.special import xlogy
from scipy import sparse
from scipy.optimize import linprog
from draper.model import fit,observation_arrays,observation_operator


def exclusive_merges(system,colocated_origins):
    """仅生成二路分支共同汇合的互斥块候选；顺序执行的块不能相加。"""
    successors={o+b['id']:{o+f['edges'][e]['dst'] for e in b['successor_edges'] if f['edges'][e]['dst']>=0}
        for f,o in zip(system.functions,system.block_offsets) for b in f['blocks']}
    parents={b:set() for b in successors}
    for b,targets in successors.items():
        for target in targets:parents[target].add(b)
    pairs=set()
    for origins in colocated_origins:
        for left in origins:
            for right in origins:
                if left>=right or len(parents[left])!=1 or parents[left]!=parents[right]:continue
                parent=next(iter(parents[left]))
                if successors[parent]=={left,right} and len(successors[left])==1 and successors[left]==successors[right]:
                    pairs.add((left,right))
    return sorted(pairs)


def relation(kind,blocks,ratios=None):
    """比例来自变换证据或独立校准，不能从参考评价计数偷取。"""
    if kind not in ('identity','inline','clone','merge-exclusive','loop'):
        raise ValueError('Unsupported transformation semantics')
    if not blocks or (kind!='merge-exclusive' and len(blocks)!=1):raise ValueError('Invalid origin count')
    if kind=='merge-exclusive':
        if ratios is not None:raise ValueError('Exclusive merge sums counts with coefficient one')
        ratios=[1.]*len(blocks)
    elif kind=='identity':ratios=[1.]
    elif ratios is None or len(ratios)!=1 or not 0<float(ratios[0])<=1:
        raise ValueError('A positive context/copy frequency ratio is required')
    return [{'block':int(b),'coefficient':float(r)} for b,r in zip(blocks,ratios)]


def infer(system,observations,initial,scenarios,anchors=()):
    """对完整相关映射候选分别求解；用泊松拟合似然调整候选先验权重。"""
    if not scenarios:raise ValueError('No mapping scenarios')
    prior=np.asarray([s['prior'] for s in scenarios],float)
    if not np.isfinite(prior).all() or (prior<=0).any():raise ValueError('Invalid mapping prior')
    prior/=prior.sum();solutions=[];scores=[];audits=[]
    for scenario,weight in zip(scenarios,prior):
        observed={**observations,'operator_rows':scenario['operator_rows']}
        samples,present,rate=observation_arrays(system,observed)
        solution,audit=fit(system,observed,initial,anchors)
        mean=np.maximum(rate*(observation_operator(system,observed)@solution),1e-12)
        log_likelihood=np.sum((xlogy(samples,mean)-mean)[present])
        solutions.append(solution);scores.append(np.log(weight)+log_likelihood);audits.append(audit)
    scores=np.asarray(scores);probabilities=np.exp(scores-scores.max());probabilities/=probabilities.sum()
    return {'estimate':probabilities@np.asarray(solutions),'solutions':np.asarray(solutions),
            'mapping_probabilities':probabilities,'audits':audits,
            'method':'plug-in Poisson likelihood weights evaluated at constrained absolute-deviation fits'}


def propagate_bounds(constraints,rhs,block_operator,scale,outgoing=None):
    """用非负线性等式传播保守上下界，避免为大型程序的每个块重复求解整个问题。"""
    constraints=constraints.tocsr();constraints.eliminate_zeros();columns=constraints.tocsc()
    lower=np.zeros(constraints.shape[1]);upper=np.full(constraints.shape[1],np.inf)
    pending=deque(range(constraints.shape[0]));queued=np.ones(constraints.shape[0],dtype=bool)
    # 提前停止只会保留更宽的范围，不会把尚未确定的变量当成已确定。
    limit=20*sum(constraints.shape);steps=0;tolerance=1e-10
    while pending and steps<limit:
        row=pending.popleft();queued[row]=False;steps+=1
        start,stop=constraints.indptr[row:row+2];indices=constraints.indices[start:stop];values=constraints.data[start:stop]
        smallest=np.where(values>0,values*lower[indices],values*upper[indices])
        largest=np.where(values>0,values*upper[indices],values*lower[indices])
        min_finite=np.isfinite(smallest);max_finite=np.isfinite(largest)
        min_sum=float(smallest[min_finite].sum());max_sum=float(largest[max_finite].sum())
        min_infinite=int((~min_finite).sum());max_infinite=int((~max_finite).sum())
        for k,(variable,coefficient) in enumerate(zip(indices,values)):
            other_min=min_sum-(smallest[k] if min_finite[k] else 0.) if min_infinite-int(not min_finite[k])==0 else -np.inf
            other_max=max_sum-(largest[k] if max_finite[k] else 0.) if max_infinite-int(not max_finite[k])==0 else np.inf
            low=(rhs[row]-(other_max if coefficient>0 else other_min))/coefficient
            high=(rhs[row]-(other_min if coefficient>0 else other_max))/coefficient
            if np.isfinite(low):low-=tolerance*(1+abs(low))
            if np.isfinite(high):high+=tolerance*(1+abs(high))
            low=max(lower[variable],low,0.);high=min(upper[variable],high)
            if low>high+1e-7:raise RuntimeError('Inconsistent interval propagation constraints')
            high=max(low,high)
            if low>lower[variable]+tolerance or high<upper[variable]-tolerance:
                lower[variable]=low;upper[variable]=high
                for neighbor in columns.indices[columns.indptr[variable]:columns.indptr[variable+1]]:
                    if not queued[neighbor]:pending.append(neighbor);queued[neighbor]=True
    block_lower=block_operator@lower;block_upper=block_operator@upper
    if outgoing is not None:
        block_lower=np.maximum(block_lower,outgoing@lower);block_upper=np.minimum(block_upper,outgoing@upper)
    return np.stack([block_lower,block_upper],axis=1)*scale


def ambiguity_bounds(system,observations,solution,anchors=()):
    """保持可见计数不变，求不可见基本块的最小、最大可行频次。"""
    _,present,_=observation_arrays(system,observations)
    operator=observation_operator(system,observations)[present]
    magnitude=abs(operator).max(axis=1).toarray().ravel()
    operator=sparse.diags(1/np.maximum(magnitude,1e-100))@operator
    scale=max(float(solution.max(initial=0)),1.)
    constraints=sparse.vstack([system.constraints,operator],format='csr')
    rhs=np.r_[np.zeros(system.constraints.shape[0]),operator@solution/scale]
    direct={row[0]['block'] for row,keep in zip(observations['operator_rows'],present) if keep and len(row)==1}
    for anchor in anchors:
        constraints=sparse.vstack([constraints,system.blocks.getrow(anchor['block'])],format='csr')
        rhs=np.r_[rhs,anchor['count']/scale];direct.add(anchor['block'])
    counts=system.blocks@solution;bounds=np.stack([counts,counts],axis=1)
    unseen=set(range(system.block_count))-direct
    if len(unseen)>128:
        totals=[sparse.csr_matrix(np.ones((1,len(f['blocks']))))@system.cfg[o:o+len(f['blocks'])]
            for f,o in zip(system.functions,system.block_offsets)]
        constraints=sparse.vstack([constraints,*totals],format='csr');rhs=np.r_[rhs,np.zeros(len(totals))]
        bounds=propagate_bounds(constraints,rhs,system.blocks,scale,system.blocks+system.cfg)
        for block in direct:bounds[block]=counts[block]
        return bounds
    for block in unseen:
        cost=system.blocks.getrow(block).toarray().ravel()
        for side,sign in enumerate((1.,-1.)):
            result=linprog(sign*cost,A_eq=constraints,b_eq=rhs,bounds=(0,None),method='highs-ds',
                options={'presolve':False,'primal_feasibility_tolerance':1e-9,'dual_feasibility_tolerance':1e-9})
            if result.status==3 and sign<0:bounds[block,side]=np.inf
            elif result.success:bounds[block,side]=max(0.,sign*result.fun*scale)
            else:raise RuntimeError('Ambiguity bounds failed: '+result.message)
    return bounds


def intervals(system,observations,initial,scenarios,repetitions=100,seed=42,anchors=(),include_ambiguity=True,progress=None):
    if repetitions<4:raise ValueError('At least four replicates required')
    if progress:progress({'phase':'initial fit','completed_draws':0,'total_draws':repetitions})
    fitted=infer(system,observations,initial,scenarios,anchors)
    random=np.random.default_rng(seed);draws=[];mapping=[]
    for _ in range(repetitions):
        index=random.choice(len(scenarios),p=fitted['mapping_probabilities'])
        observed={**copy.deepcopy(observations),'operator_rows':scenarios[index]['operator_rows']}
        _,present,rate=observation_arrays(system,observed)
        mean=rate*(observation_operator(system,observed)@fitted['solutions'][index])+.5
        sample=random.poisson(mean);sample[~present]=0;observed['samples']=sample.tolist()
        refitted=infer(system,observed,initial,scenarios,anchors)
        selected=random.choice(len(scenarios),p=refitted['mapping_probabilities'])
        draws.append(refitted['solutions'][selected]);mapping.append(selected)
        if progress:progress({'phase':'resampling','completed_draws':len(draws),'total_draws':repetitions})
    draws=np.asarray(draws)
    block_intervals=np.quantile((system.blocks@draws.T).T,[.025,.975],axis=0).T
    lower_margin=np.zeros(system.block_count);upper_margin=np.zeros(system.block_count)
    if include_ambiguity:
        for index,(scenario,solution,probability) in enumerate(zip(scenarios,fitted['solutions'],fitted['mapping_probabilities'])):
            if probability<1e-6:continue
            if progress:progress({'phase':'ambiguity bounds','scenario':index+1,'total_scenarios':len(scenarios)})
            bounds=ambiguity_bounds(system,{**observations,'operator_rows':scenario['operator_rows']},solution,anchors)
            counts=system.blocks@solution
            lower_margin=np.maximum(lower_margin,counts-bounds[:,0]);upper_margin=np.maximum(upper_margin,bounds[:,1]-counts)
        block_intervals[:,0]=np.maximum(0,block_intervals[:,0]-lower_margin)
        block_intervals[:,1]+=upper_margin
    unknown=np.flatnonzero(lower_margin+upper_margin>1e-5*(1+system.blocks@fitted['estimate'])).tolist()
    if progress:progress({'phase':'complete','completed_draws':repetitions,'total_draws':repetitions})
    return {**fitted,'draws':draws,'edge_intervals':np.quantile(draws,[.025,.975],axis=0).T,
            'block_intervals':block_intervals,'unidentified_blocks':unknown,'mapping_draws':mapping}


def coverage(bounds,truth):
    bounds=np.asarray(bounds);truth=np.asarray(truth)
    if bounds.shape!=(len(truth),2) or not np.isfinite(bounds[:,0]).all() or np.isnan(bounds[:,1]).any() or np.any(bounds[:,0]>bounds[:,1]):
        raise ValueError('Invalid evaluation interval')
    covered=(bounds[:,0]<=truth)&(truth<=bounds[:,1]);positive=truth>0
    finite=np.isfinite(bounds[:,1])
    return {'coverage':float(covered.mean()),'positive_coverage':float(covered[positive].mean()) if positive.any() else None,
            'mean_width':float(np.mean(bounds[:,1]-bounds[:,0])) if finite.all() else None,'count':len(truth),
            'unbounded_fraction':float(np.mean(~finite)),
            'finite_coverage':float(covered[finite].mean()) if finite.any() else None}


def mapping_score(probabilities,correct):
    probabilities=np.asarray(probabilities,float)
    if not 0<=correct<len(probabilities) or not np.isfinite(probabilities).all() or (probabilities<0).any() or not np.isclose(probabilities.sum(),1):
        raise ValueError('Invalid mapping evaluation')
    target=np.zeros(len(probabilities));target[correct]=1
    tied=np.isclose(probabilities,probabilities.max(),rtol=1e-8,atol=1e-10)
    return {'brier':float(np.sum((probabilities-target)**2)),'log_loss':float(-np.log(max(probabilities[correct],1e-15))),
            'top_one_correct':float(tied[correct]/tied.sum()),'unresolved_tie':bool(tied.sum()>1)}


def calibrate_width(bounds,truth,alpha=.05,min_half_width=1.):
    """使用独立校准运行学习区间扩张系数；每个运行整体作为一个样本。"""
    bounds=np.asarray(bounds,float);truth=np.asarray(truth,float)
    if bounds.ndim!=3 or bounds.shape[2]!=2 or truth.shape!=bounds.shape[:2] or not 0<alpha<1 or min_half_width<=0:
        raise ValueError('Invalid interval calibration data')
    finite=np.isfinite(bounds[:,:,1]);safe=np.where(finite[:,:,None],bounds,0.)
    center=safe.mean(axis=2);half=(safe[:,:,1]-safe[:,:,0])/2
    scores=np.max(np.where(finite,np.abs(truth-center)/np.maximum(half,min_half_width),0.),axis=1)
    order=int(np.ceil((len(scores)+1)*(1-alpha)))
    if order>len(scores):raise ValueError('More independent calibration runs required')
    return max(1.,float(np.sort(scores)[order-1]))


def widen(bounds,multiplier,min_half_width=1.):
    bounds=np.asarray(bounds,float)
    if not np.isfinite(multiplier) or multiplier<1 or min_half_width<=0:raise ValueError('Invalid interval multiplier')
    finite=np.isfinite(bounds[:,1]);safe=np.where(finite[:,None],bounds,0.)
    center=safe.mean(axis=1);half=np.maximum((safe[:,1]-safe[:,0])/2,min_half_width)*multiplier
    return np.where(finite[:,None],np.stack([np.maximum(center-half,0),center+half],axis=1),bounds)
