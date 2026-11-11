"""在优化后程序上独立计数，用一次校准运行估计副本比例，再恢复另一运行的早期频次。"""
import argparse,copy,json,os,shutil,subprocess,time
from collections import defaultdict
from pathlib import Path
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
import numpy as np
from draper.evidence import probes,transformation_evidence
from draper.model import FlowSystem,evaluate
from draper.transformed import intervals,coverage,exclusive_merges,relation
from draper.workload import execute,verify
from run_draper import save


def main():
 p=argparse.ArgumentParser();p.add_argument('experiment',type=Path);p.add_argument('--program',required=True)
 p.add_argument('--bridge',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
 p.add_argument('--draws',type=int,default=40);p.add_argument('--ratio-scenarios',type=int,default=5)
 p.add_argument('--pilot-execution',type=Path,help='Optional independent calibration input description')
 a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);os.nice(10)
 implementation=out/'implementation';implementation.mkdir()
 for file in ('collect_transformed_observations.py','draper/transformed.py','draper/model.py','draper/llvm_bridge.cpp','draper/workload.py'):
  shutil.copyfile(file,implementation/Path(file).name)
 source=a.experiment.resolve()/a.program;bridge=a.bridge.resolve();commands=[]
 entry=next(r for r in json.loads((a.experiment/'summary.json').read_text())['programs'] if r['program']==a.program)
 execution=entry['execution'];pilot=json.loads(a.pilot_execution.read_text()) if a.pilot_execution else execution
 def run(argv):
  start=time.monotonic();r=subprocess.run(list(map(str,argv)),capture_output=True,timeout=600)
  commands.append({'argv':list(map(str,argv)),'returncode':r.returncode,'seconds':time.monotonic()-start,'stderr':r.stderr.decode(errors='replace')})
  save(out/'commands.json',commands)
  if r.returncode:raise RuntimeError(r.stderr.decode(errors='replace')[-3000:])
 graph=json.loads((source/'graph.json').read_text());system=FlowSystem(graph)
 run(['opt','-passes=pseudo-probe','-S',source/'canonical.ll','-o',out/'probes.ll'])
 run(['opt','-passes=default<O3>','-S',out/'probes.ll','-o',out/'optimized.ll',f'-pass-remarks-output={out / "optimization.remarks.yaml"}'])
 evidence=transformation_evidence((out/'probes.ll').read_text(),(out/'optimized.ll').read_text())
 save(out/'transformation-evidence.json',evidence)
 run([bridge,'probe-prepare',out/'optimized.ll',f'--output={out / "instrumented.ll"}',f'--graph={out / "probe-graph.json"}'])
 run(['opt','-passes=instrprof',*(['-instrprof-atomic-counter-update-all'] if execution.get('atomic_counters') else []),
  out/'instrumented.ll','-o',out/'instrumented.bc'])
 run([execution['compiler'],'-O3',out/'instrumented.bc','-fprofile-instr-generate',*execution['link_flags'],'-o',out/'instrumented'])
 measured=[];measurement_runs=[]
 for repeat,spec in enumerate((pilot,execution)):
  raw=out/f'run-{repeat}.profraw';profile=out/f'run-{repeat}.profdata';counts=out/f'run-{repeat}.json'
  result=execute(out/'instrumented',spec,{'LLVM_PROFILE_FILE':str(raw)})
  measurement_runs.append({'run':repeat,'seconds':result.elapsed_seconds,'role':'ratio calibration' if repeat==0 else 'observation collection'})
  verify(result,spec,out/f'run-{repeat}.output')
  run(['llvm-profdata','merge',raw,'-o',profile])
  run([bridge,'probe-truth',out/'optimized.ll',f'--graph={out / "probe-graph.json"}',f'--profile={profile}',f'--output={counts}'])
  measured.append(json.loads(counts.read_text())['occurrences'])
 positions={(f['name'],b['name'] if 'name' in b else f'draper.bb.{b["id"]}'):o+b['id'] for f,o in zip(system.functions,system.block_offsets) for b in f['blocks']}
 origins=defaultdict(set)
 for probe in probes((out/'probes.ll').read_text()):
  location=(probe['function'],probe['block'])
  if location in positions:origins[(probe['guid'],probe['probe'])].add(positions[location])
 groups=defaultdict(list);unknown=[]
 for i,row in enumerate(measured[0]):
  origin=origins[(int(row['guid']),row['probe'])]
  if len(origin)==1:groups[next(iter(origin))].append(i)
  else:unknown.append(i)
 if not groups:raise ValueError('No uniquely mapped optimized probes')
 selected=[i for indices in groups.values() for i in indices]
 random=np.random.default_rng(42);rows_by_scenario=[[] for _ in range(a.ratio_scenarios)]
 ratios=[]
 for block,indices in groups.items():
  alpha=np.array([measured[0][i]['count']+.5 for i in indices],float)
  shares=[alpha/alpha.sum(),*[random.dirichlet(alpha) for _ in range(a.ratio_scenarios-1)]]
  for rows,share in zip(rows_by_scenario,shares):
   rows.extend([[{'block':block,'coefficient':float(ratio)}] for ratio in share])
  ratios.append({'block':block,'occurrences':indices,'pilot_counts':[measured[0][i]['count'] for i in indices],'mean_ratios':shares[0].tolist()})
 # 条件被折叠后，探针可能移动到后继块。共同所在的优化后块提供第二种映射候选。
 successors={o+b['id']:{o+f['edges'][e]['dst'] for e in b['successor_edges'] if f['edges'][e]['dst']>=0}
  for f,o in zip(system.functions,system.block_offsets) for b in f['blocks']}
 colocated=defaultdict(list)
 for j,i in enumerate(selected):colocated[(measured[0][i]['function'],measured[0][i]['block'])].append(j)
 ambiguities=[]
 for j,i in enumerate(selected):
  block=rows_by_scenario[0][j][0]['block']
  if len(successors[block])<2 or len(groups[block])!=1:continue
  candidates=[k for k in colocated[(measured[0][i]['function'],measured[0][i]['block'])]
    if rows_by_scenario[0][k][0]['block'] in successors[block]]
  if len(candidates)==1:ambiguities.append({'row':j,'candidate_row':candidates[0],'original_block':block,
    'candidate_block':rows_by_scenario[0][candidates[0]][0]['block']})
 scenarios=[{'prior':1.,'mapping':'retained origin','operator_rows':rows} for rows in rows_by_scenario]
 if ambiguities:
  for original in rows_by_scenario:
   changed=copy.deepcopy(original)
   for ambiguity in ambiguities:changed[ambiguity['row']]=copy.deepcopy(original[ambiguity['candidate_row']])
   scenarios.append({'prior':1.,'mapping':'colocated original successor','operator_rows':changed})
 merge_pairs=exclusive_merges(system,[{rows_by_scenario[0][j][0]['block'] for j in js
    if len(groups[rows_by_scenario[0][j][0]['block']])==1} for js in colocated.values()])
 if merge_pairs:
  indices={row[0]['block']:j for j,row in enumerate(rows_by_scenario[0]) if len(groups[row[0]['block']])==1}
  for scenario in list(scenarios):
   changed=copy.deepcopy(scenario)
   for left,right in merge_pairs:
    changed['operator_rows'][indices[left]]=relation('merge-exclusive',[left,right])
    changed['operator_rows'][indices[right]]=relation('merge-exclusive',[left,right])
   changed['mapping']+=' + exclusive merge';scenarios.append(changed)
 counts=np.array([measured[1][i]['count'] for i in selected],float)
 observed={'ir_id':graph['ir_id'],'rate':1/64,'samples':random.poisson(counts/64).tolist(),
   'present':[True]*len(selected),'operator_rows':rows_by_scenario[0],
   'kind':'Poisson thinning of separately measured post-optimization occurrence counts'}
 # 初值只使用优化后观测的总和；精确早期计数在求解完成后才读取。
 aggregate=np.zeros(system.block_count)
 for row,sample in zip(observed['operator_rows'],observed['samples']):aggregate[row[0]['block']]+=sample
 offset=0;functions=[]
 for f in system.functions:
  n=len(f['blocks']);functions.append({'name':f['name'],'samples':aggregate[offset:offset+n].tolist(),'present':[offset+i in groups for i in range(n)]});offset+=n
 initial_observation={'ir_id':graph['ir_id'],'rate':1/64,'functions':functions}
 save(out/'initial-observations.json',initial_observation)
 run([bridge,'profi',source/'canonical.ll',f'--graph={source / "graph.json"}',f'--profile={out / "initial-observations.json"}',f'--output={out / "initial.json"}'])
 initial=system.vector(json.loads((out/'initial.json').read_text()))
 save(out/'observations.json',observed);save(out/'ratio-scenarios.json',scenarios);save(out/'ratios.json',ratios)
 save(out/'mapping-alternatives.json',ambiguities)
 save(out/'exclusive-merge-candidates.json',merge_pairs)
 fit_start=time.monotonic()
 fitted=intervals(system,observed,initial,scenarios,repetitions=a.draws,seed=42,
    progress=lambda state:save(out/'fit-progress.json',{**state,'seconds':time.monotonic()-fit_start}))
 save(out/'restored.json',system.profile(fitted['estimate'],'transformed occurrence observation fit'))
 np.savez_compressed(out/'uncertainty.npz',edges=fitted['draws'],blocks=fitted['block_intervals'])
 reference=json.loads((source/'exact-0.json').read_text());truth=system.vector(reference);blocks=system.blocks@truth
 conservation=[]
 for block,indices in groups.items():
  total=sum(measured[1][i]['count'] for i in indices)
  conservation.append({'block':block,'optimized_total':total,'early_reference':float(blocks[block]),'difference':float(total-blocks[block])})
 save(out/'probe-count-conservation.json',conservation)
 mapping_evaluation=[]
 for item in ambiguities:
  errors=[]
  for scenario in scenarios:
   terms=scenario['operator_rows'][item['row']]
   expected=sum(blocks[t['block']]*t['coefficient'] for t in terms)
   errors.append(abs(expected-counts[item['row']])/max(counts[item['row']],1))
  compatible=np.asarray(errors)<.01
  mapping_evaluation.append({**item,'relative_errors':errors,'compatible_probability':float(fitted['mapping_probabilities'][compatible].sum()),
    'compatible_scenarios':int(compatible.sum())})
 save(out/'mapping-evaluation.json',mapping_evaluation)
 save(out/'summary.json',{'status':'complete','program':a.program,'observation_rows':len(selected),'mapped_blocks':len(groups),
  'unknown_occurrences':len(unknown),'ratio_scenarios':a.ratio_scenarios,'draws':a.draws,
  'calibration_scope':'independent input' if a.pilot_execution else 'separate execution of the same input',
  'calibration_cost':'all retained optimized probe occurrences instrumented; outside sparse-counter budget',
  'measurement_runs':measurement_runs,
  'count_conservation_failures':sum(r['difference']!=0 for r in conservation),
  'mapping_alternatives':len(ambiguities),'mapping_scenarios':len(scenarios),
  'exclusive_merge_candidates':len(merge_pairs),'unidentified_blocks':fitted['unidentified_blocks'],
  'mean_count_compatible_mapping_probability':float(np.mean([m['compatible_probability'] for m in mapping_evaluation])) if mapping_evaluation else None,
  'coverage_all_blocks':coverage(fitted['block_intervals'],blocks),
  'coverage_mapped_blocks':coverage(fitted['block_intervals'][list(groups)],blocks[list(groups)]),
  'scenario_probabilities':fitted['mapping_probabilities'].tolist(),'flow':system.audit(fitted['estimate']),
  'count_error':evaluate(system,reference,fitted['estimate'])})
 experiment=out/'experiment';directory=experiment/a.program;directory.mkdir(parents=True)
 for name in ('canonical.ll','graph.json','exact-0.json','exact-1.json'):
  shutil.copyfile(source/name,directory/name)
 for name,target in (('initial.json','profi.json'),('restored.json','restored.json'),('restored.json','calibrated.json'),
   ('observations.json','observations.json'),('ratio-scenarios.json','mapping-scenarios.json')):
  shutil.copyfile(out/name,directory/target)
 save(directory/'anchors.json',{'anchors':[]})
 for variant in ('profi','restored','calibrated'):
  run([bridge,'annotate',directory/'canonical.ll',f'--graph={directory / "graph.json"}',f'--profile={directory / (variant+".json")}',
    f'--output={directory / (variant+".ll")}'])
 save(experiment/'summary.json',{'status':'complete','programs':[entry]})


if __name__=='__main__':main()
