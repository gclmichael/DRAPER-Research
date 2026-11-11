"""编译器决策回放、静态代价和硬预算修正；不使用试运行时间挑版本。"""
import argparse
import filecmp
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
for key in ("OPENBLAS_NUM_THREADS","OMP_NUM_THREADS","MKL_NUM_THREADS"):
    os.environ[key]="1"
import numpy as np
from draper.model import FlowSystem, uncertainty, evaluate, fit, risks,anchored_profi
from draper.decision_risk import linear_decisions, bounded_projection, budgeted_anchors, replay_acceptance
from run_draper import ROOT,save
from analyze_remarks import read_events
from draper.evidence import transformation_evidence
from draper.workload import execute as execute_workload,verify as verify_workload
from draper.transformed import infer as infer_transformed,intervals as transformed_intervals,ambiguity_bounds


def machine_order(text, system):
    """只读取保留早期块名的机器基本块；优化生成或合并后无法唯一对应的块跳过。"""
    layouts={};conditional={}; function=None; body=False;current=None
    for line in text.splitlines():
        match=re.match(r'^name:\s+[\'"]?([^\s\'"]+)',line)
        if match:function=match[1];body=False;layouts[function]=[];conditional[function]=set();current=None
        if line.startswith('body:'):body=True
        if body:
            if re.match(r'  bb\.\d+[^:]*:',line):
                match=re.match(r'  bb\.\d+\.draper\.bb\.(\d+)(?=[: (])',line)
                current=int(match[1]) if match else None;layouts[function].append(current)
            if current is not None and line.strip().startswith('successors:') and len(set(re.findall(r'%bb\.\d+',line)))>=2:
                conditional[function].add(current)
    actions={}
    for f,eo in zip(system.functions,system.edge_offsets):
        layout=layouts.get(f['name'],[])
        for source,target in zip(layout,layout[1:]):
            if source is None or target is None or source>=len(f['blocks']) or source not in conditional.get(f['name'],set()):continue
            ids=f['blocks'][source]['successor_edges']
            if len(ids)!=2:continue
            left,right=ids
            destinations=[f['edges'][e]['dst'] for e in ids]
            if target in destinations and destinations[0]!=destinations[1]:
                actions[('successor-order',f'{eo+left}:{eo+right}')]=target==destinations[0]
    return actions


def main():
    p=argparse.ArgumentParser();p.add_argument('experiment',type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--programs',nargs='+');p.add_argument('--budget',type=float,default=.01)
    p.add_argument('--fresh-calibration',action='store_true',help='Select and execute new sparse counters under an estimated update budget')
    p.add_argument('--bridge',type=Path)
    p.add_argument('--bootstrap',type=int,default=20)
    p.add_argument('--resume',action='store_true',help='Continue compiler replay from saved calibrated counts and uncertainty draws')
    p.add_argument('--command-timeout',type=int,default=900)
    a=p.parse_args();source=a.experiment.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=a.resume)
    os.nice(10);commands=json.loads((out/'commands.json').read_text()) if a.resume else []
    summary={'status':'incomplete','seed':42,'budget_fraction':a.budget,'programs':[]}
    def run(argv,env=None):
        t=time.monotonic()
        try:r=subprocess.run(list(map(str,argv)),capture_output=True,text=True,timeout=a.command_timeout,env={**os.environ,**(env or {})})
        except subprocess.TimeoutExpired:
            commands.append({'argv':list(map(str,argv)),'seconds':time.monotonic()-t,'returncode':None,'timeout':a.command_timeout})
            save(out/'commands.json',commands);raise
        commands.append({'argv':list(map(str,argv)),'seconds':time.monotonic()-t,'returncode':r.returncode,'stderr':r.stderr})
        save(out/'commands.json',commands)
        if r.returncode:raise RuntimeError(r.stderr[-4000:])
        return r.stdout
    bridge=out/'draper-llvm'
    if a.bridge:shutil.copyfile(a.bridge,bridge);bridge.chmod(0o755)
    else:
        flags=shlex.split(run(['llvm-config','--cxxflags','--ldflags','--libs','core','passes','analysis','profiledata','transformutils','irreader']))
        run(['clang++',ROOT/'draper/llvm_bridge.cpp',*flags,'-O1','-o',bridge])
    implementation=out/(f'implementation-resume-{1+len(list(out.glob("implementation-resume-*")))}' if a.resume else 'implementation');implementation.mkdir()
    for name in ('run_core_risk.py','draper/decision_risk.py','draper/model.py','draper/transformed.py','draper/evidence.py','draper/llvm_bridge.cpp','analyze_remarks.py','draper/workload.py'):
        shutil.copyfile(ROOT/name,implementation/Path(name).name)
    original=json.loads((source/'summary.json').read_text())
    try:
        for entry in original['programs']:
            name=entry['program']
            if a.programs and name not in a.programs:continue
            execution=entry.get('execution',{})
            compiler=execution.get('compiler','clang');link_flags=execution.get('link_flags',['-lm'])
            def execute(binary,env=None):
                r=execute_workload(binary,execution,env)
                if execution:
                    verify_workload(r,execution,directory/'last-output.txt')
                elif r.returncode:raise RuntimeError('Benchmark self-check failed')
            old=source/name;directory=out/name;directory.mkdir(exist_ok=a.resume)
            if a.resume:
                for file in ('canonical.ll','graph.json'):
                    if not filecmp.cmp(old/file,directory/file,shallow=False):raise ValueError('Resume input differs from saved experiment')
            else:
                for file in ('canonical.ll','graph.json','profi.ll','restored.ll','calibrated.ll'):
                    shutil.copyfile(old/file,directory/file)
            system=FlowSystem(json.loads((old/'graph.json').read_text()))
            if not a.resume:
                run(['opt','-passes=pseudo-probe','-S',directory/'canonical.ll','-o',directory/'probes.ll'])
                run(['opt','-passes=default<O3>','-S',directory/'probes.ll','-o',directory/'optimized-probes.ll'])
                save(directory/'transformation-evidence.json',transformation_evidence(
                    (directory/'probes.ll').read_text(),(directory/'optimized-probes.ll').read_text()))
            observations=json.loads((old/'observations.json').read_text())
            initial=system.vector(json.loads((old/'profi.json').read_text()))
            mapping_path=old/'mapping-scenarios.json'
            mapping_scenarios=json.loads(mapping_path.read_text()) if mapping_path.exists() else None
            mapping_unknown=[];mapping_upper=None
            def draw_counts(center,measured):
                nonlocal mapping_unknown,mapping_upper
                if mapping_scenarios:
                    started=time.monotonic()
                    result=transformed_intervals(system,observations,initial,mapping_scenarios,
                        repetitions=a.bootstrap,seed=42,anchors=measured,
                        progress=lambda state:save(directory/'uncertainty-progress.json',
                            {**state,'measured_anchors':len(measured),'seconds':time.monotonic()-started}))
                    save(directory/'mapping-probabilities.json',result['mapping_probabilities'].tolist())
                    mapping_unknown=result['unidentified_blocks'];mapping_upper=result['block_intervals'][:,1]
                    np.savez_compressed(directory/'block-uncertainty.npz',bounds=result['block_intervals'])
                    save(directory/'mapping-uncertainty.json',{'unidentified_blocks':mapping_unknown,'measured_anchors':measured})
                    return result['draws']
                return uncertainty(system,observations,initial,center,a.bootstrap,42,anchors=measured)
            if a.resume:
                prepared=directory/'prepared-counts.json'
                if not prepared.exists():prepared=directory/'center.json'
                x=system.vector(json.loads(prepared.read_text()))
                anchors=json.loads((directory/'anchors.json').read_text())['anchors']
                calibration=json.loads((directory/'calibration.json').read_text()) if (directory/'calibration.json').exists() else None
                with np.load(directory/'calibrated-bootstrap.npz') as saved:draws=saved['edges']
                if draws.shape!=(a.bootstrap,system.edge_count):raise ValueError('Saved uncertainty draws do not match requested run')
                if mapping_scenarios:
                    with np.load(directory/'block-uncertainty.npz') as saved:mapping_upper=saved['bounds'][:,1]
                    checkpoint=directory/'mapping-uncertainty.json'
                    if checkpoint.exists():
                        restored=json.loads(checkpoint.read_text())
                        if restored['measured_anchors']!=anchors:raise ValueError('Saved mapping uncertainty uses different anchors')
                        mapping_unknown=restored['unidentified_blocks']
                    else:
                        # Older runs saved draws but not the ambiguity list; reconstruct only this deterministic part.
                        fitted=infer_transformed(system,observations,initial,mapping_scenarios,anchors)
                        lower=np.zeros(system.block_count);upper=np.zeros(system.block_count)
                        for scenario,solution,probability in zip(mapping_scenarios,fitted['solutions'],fitted['mapping_probabilities']):
                            if probability<1e-6:continue
                            bounds=ambiguity_bounds(system,{**observations,'operator_rows':scenario['operator_rows']},solution,anchors)
                            counts=system.blocks@solution
                            lower=np.maximum(lower,counts-bounds[:,0]);upper=np.maximum(upper,bounds[:,1]-counts)
                        mapping_unknown=np.flatnonzero(lower+upper>1e-5*(1+system.blocks@fitted['estimate'])).tolist()
                        save(checkpoint,{'unidentified_blocks':mapping_unknown,'measured_anchors':anchors})
                save(directory/'resume-preparation.json',{'reused_calibrated_counts':str(prepared),'reused_draws':len(draws),'anchors':len(anchors)})
            else:
                x=system.vector(json.loads((old/'calibrated.json').read_text()))
                anchors=json.loads((old/'anchors.json').read_text())['anchors']
                calibration=None
                if a.fresh_calibration:
                    x=system.vector(json.loads((old/'restored.json').read_text()))
                    prior_draws=draw_counts(x,[])
                    prior_decisions=[d for d in linear_decisions(system,x,prior_draws,include_stable=bool(mapping_unknown))
                        if d['kind']!='block-frequency']
                    for decision in prior_decisions:
                        if decision['block'] in mapping_unknown:decision['probability_positive']=.5
                    upper=np.quantile((system.blocks@prior_draws.T).T,.975,axis=0)
                    if mapping_upper is not None:upper=np.maximum(upper,mapping_upper)
                    calibration=budgeted_anchors(system,prior_decisions,x,upper_counts=upper)
                    calibration['prior_unidentified_blocks']=mapping_unknown.copy()
                    save(directory/'selected-blocks.json',{'ir_id':system.graph['ir_id'],'blocks':calibration['blocks']})
                    anchors=[]
                    if calibration['blocks']:
                        run([bridge,'calibrate',directory/'canonical.ll',f'--graph={directory / "graph.json"}',
                             f'--selection={directory / "selected-blocks.json"}',f'--output={directory / "sparse.ll"}'])
                        run(['opt','-passes=instrprof',*(['-instrprof-atomic-counter-update-all'] if execution.get('atomic_counters') else []),
                            directory/'sparse.ll','-o',directory/'sparse.bc'])
                        run([compiler,'-O3',directory/'sparse.bc','-fprofile-instr-generate',*link_flags,'-o',directory/'sparse'])
                        execute(directory/'sparse',{'LLVM_PROFILE_FILE':str(directory/'sparse.profraw')})
                        run(['llvm-profdata','merge',directory/'sparse.profraw','-o',directory/'sparse.profdata'])
                        run([bridge,'read-anchors',directory/'canonical.ll',f'--graph={directory / "graph.json"}',
                             f'--selection={directory / "selected-blocks.json"}',f'--profile={directory / "sparse.profdata"}',
                             f'--output={directory / "anchors.json"}'])
                        anchors=json.loads((directory/'anchors.json').read_text())['anchors']
                    if mapping_scenarios:
                        fitted=infer_transformed(system,observations,initial,mapping_scenarios,anchors)
                        x=fitted['estimate'];calibration['fit']={'scenario_audits':fitted['audits']}
                    else:x,calibration['fit']=fit(system,observations,initial,anchors=anchors)
                    calibration['measured_dynamic_updates']=sum(v['count'] for v in anchors)
                    calibration['measured_exceeds_estimated_budget']=calibration['measured_dynamic_updates']>calibration['dynamic_budget']
                    save(directory/'calibration.json',calibration)
                    save(directory/'anchors.json',{'anchors':anchors})
                draws=draw_counts(x,anchors)
                np.savez_compressed(directory/'calibrated-bootstrap.npz',edges=draws)
            save(directory/'prepared-counts.json',system.profile(x,'calibrated counts before compiler replay'))
            def replay(vector,label):
                prefix=directory/label
                cached=directory/(label+'.replay.json')
                if a.resume and cached.exists() and prefix.with_suffix('.json').exists():
                    previous=system.vector(json.loads(prefix.with_suffix('.json').read_text()))
                    if np.array_equal(previous,vector):
                        value=json.loads(cached.read_text())
                        return {tuple(key):action for key,action in value['actions']},value['inlines']
                save(directory/'replay-progress.json',{'label':label,'phase':'compiling'})
                save(prefix.with_suffix('.json'),system.profile(vector,'bounded decision risk replay'))
                run([bridge,'annotate',directory/'canonical.ll',f'--graph={directory / "graph.json"}',
                     f'--profile={prefix.with_suffix(".json")}',f'--output={prefix.with_suffix(".ll")}',
                     f'--decisions={prefix.with_suffix(".decisions.json")}'])
                r=json.loads(prefix.with_suffix('.decisions.json').read_text());actions={};inlines={}
                by_name={f['name']:(f,eo) for f,eo in zip(system.functions,system.edge_offsets)}
                for d in r['decisions']:
                    f,eo=by_name[d['function']];bi=int(d['block'].rsplit('.',1)[1])
                    actions[('branch',str(eo+f['blocks'][bi]['successor_edges'][d['slot']]))]=d['hot']
                run(['opt','-passes=default<O3>','-S',prefix.with_suffix('.ll'),'-o',prefix.with_suffix('.optimized.ll'),
                     f'-pass-remarks-output={prefix.with_suffix(".remarks.yaml")}'])
                # 读取真正优化过程的成本/阈值；只使用可唯一对应到原调用块的记录。
                seen_inline=set();ambiguous_inline=set()
                for event in read_events(prefix.with_suffix('.remarks.yaml')):
                    if event['pass']!='inline' or event['threshold_minus_cost'] is None or event['type'] not in ('Passed','Missed'):
                        continue
                    if event['function'] not in by_name or event['callee'] not in by_name:continue
                    f,_=by_name[event['function']]
                    blocks=[b for b in f['blocks'] if event['callee'] in b['calls'] and any(
                        loc['line']==event['line'] and Path(loc['file']).name==Path(event['file']).name
                        for loc in b.get('source_locations',[]))]
                    if len(blocks)!=1:continue
                    key=f'pipeline:{event["function"]}:{blocks[0]["id"]}:{event["callee"]}:{event["line"]}:{event["column"]}'
                    if key in seen_inline:
                        ambiguous_inline.add(key)
                    seen_inline.add(key)
                    actions[('inline',key)]=event['type']=='Passed'
                    inlines[key]={'function':event['function'],'block':blocks[0]['id'],
                        'callee_instructions':sum(b['instructions'] for b in by_name[event['callee']][0]['blocks']),
                        'margin':event['threshold_minus_cost']}
                for key in ambiguous_inline:
                    actions.pop(('inline',key),None);inlines.pop(key,None)
                run(['llc','-O=3','-relocation-model=pic','-stop-after=block-placement',prefix.with_suffix('.optimized.ll'),'-o',prefix.with_suffix('.mir')])
                layout=machine_order(prefix.with_suffix('.mir').read_text(),system);actions.update(layout)
                surviving={edge for _,key in layout for edge in key.split(':')}
                actions={key:value for key,value in actions.items() if key[0]!='branch' or key[1] in surviving}
                save(cached,{'actions':[[list(key),bool(value)] for key,value in actions.items()],'inlines':inlines})
                save(directory/'replay-progress.json',{'label':label,'phase':'complete'})
                return actions,inlines
            print(f'{name}: compiler decision replay, {a.bootstrap} anchored uncertainty draws',flush=True)
            base_actions,base_inline=replay(x,'center')
            for suffix in ('.ll','.json'):
                shutil.copyfile(directory/('center'+suffix),directory/('calibrated'+suffix))
            samples=[replay(row,f'draw-{i:02d}') for i,row in enumerate(draws)]
            inline=[]
            for key,r in base_inline.items():
                if all(key in v[1] for v in samples):
                    inline.append({'key':key,'function':r['function'],'block':r['block'],
                                   'instructions':r['callee_instructions'],'margins':[v[1][key]['margin'] for v in samples]})
            decisions=linear_decisions(system,x,draws,inline,include_stable=True)
            usable=[];preserve=[];unknown_keys=[]
            for d in decisions:
                key=(d['kind'],d['name'])
                if key not in base_actions or not all(key in v[0] for v in samples):continue
                if d['block'] in mapping_unknown:
                    unknown_keys.append(key)
                    if bool((d['row']@x).item()>d['threshold'])==base_actions[key]:
                        d['desired']=base_actions[key];d['safe_margin']=0;preserve.append(d)
                    continue
                d['probability_positive']=float(np.mean([v[0][key] for v in samples]))
                d['desired']=d['probability_positive']>=.5
                if d['probability_positive'] in (0,1):
                    linear_action=bool((d['row']@x).item()>d['threshold'])
                    if d['desired']==base_actions[key] and linear_action==base_actions[key]:preserve.append(d)
                    continue
                if d['kind']=='successor-order':d['source']='actual LLVM machine block placement; local flow linearization'
                usable.append(d)
            candidate,audit=bounded_projection(system,x,usable,a.budget,anchors,preserve=preserve)
            audit['stable_decision_constraints']=len(preserve)
            actual,_=replay(candidate,'proposed')
            replay_audit=replay_acceptance(base_actions,[v[0] for v in samples],actual,usable)
            replay_audit['changed_unknown_decisions']=[key for key in unknown_keys if actual.get(key)!=base_actions[key]]
            if replay_audit['changed_unknown_decisions']:replay_audit['accepted']=False
            before=replay_audit['expected_loss_before'];after=replay_audit['expected_loss_proposed']
            accept=replay_audit['accepted']
            chosen=candidate if accept else x
            selected_label='proposed' if accept else 'center'
            minimal=anchored_profi(system,initial,anchors)
            minimal_change=float(np.abs(minimal-x).sum())
            minimal_check={'distance_from_calibrated':minimal_change,'selected':False}
            if minimal_change<=a.budget*max(float(x.sum()),1) and np.abs(minimal-initial).sum()+1e-8<np.abs(chosen-initial).sum():
                minimal_actions,_=replay(minimal,'minimal-change')
                minimal_check['same_compiler_decisions']=minimal_actions==(actual if accept else base_actions)
                if minimal_check['same_compiler_decisions']:
                    chosen=minimal;selected_label='minimal-change';minimal_check['selected']=True
            for suffix in ('.ll','.json','.decisions.json'):
                shutil.copyfile(directory/(selected_label+suffix),directory/('risk-projected'+suffix))
            exported,branch_risks=risks(system,observations,chosen,draws)
            exported['kind']='bounded decision-risk output; intervals describe the calibrated count uncertainty before projection'
            save(directory/'risk-projected.json',exported)
            save(directory/'branch-risk-intervals.json',branch_risks)
            run([compiler,'-O3',directory/'risk-projected.ll',*link_flags,'-o',directory/'risk-projected'])
            execute(directory/'risk-projected')
            selection=budgeted_anchors(system,usable,chosen)
            save(directory/'next-calibration.json',selection)
            save(directory/'risk-decisions.json',[{**d,'row':{'indices':d['row'].indices.tolist(),'values':d['row'].data.tolist(),'columns':system.edge_count}} for d in usable])
            # 只在方法与选择全部固定后读取参考计数用于评价。
            truth=json.loads((old/'exact-0.json').read_text())
            report={'program':name,'projection':audit,'compiler_replay_accepted':bool(accept),
                    'compiler_expected_loss_before':before,'compiler_expected_loss_proposed':after,
                    'minimal_change_candidate':minimal_check,'final_strategy':selected_label,
                    'compiler_replay':replay_audit,'final_absolute_change':float(np.abs(chosen-x).sum()),
                    'risk_decisions_by_kind':{k:sum(d['kind']==k for d in usable) for k in ('branch','block-frequency','inline','successor-order')},
                    'base_decisions_by_kind':{k:sum(t[0]==k for t in base_actions) for k in ('branch','block-frequency','inline','successor-order')},
                    'calibrated':evaluate(system,truth,x),'risk_projected':evaluate(system,truth,chosen),
                    'next_calibration':selection,'executed_calibration':calibration,'execution':execution}
            report['unidentified_blocks_after_calibration']=mapping_unknown
            save(directory/'report.json',report);summary['programs'].append(report);save(out/'summary.json',summary)
            print(f'{name}: replay accepted={accept}, risk decisions={len(usable)}',flush=True)
        summary['status']='complete'
    finally:save(out/'summary.json',summary)


if __name__=='__main__':main()
