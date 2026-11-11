"""独立模拟采样重复实验；真值仅用于生成观测与最后评价。"""
import argparse,json,os
from pathlib import Path
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
import numpy as np
from draper.model import FlowSystem,observation_operator
from draper.transformed import relation,intervals,coverage,mapping_score,calibrate_width,widen
from run_draper import save


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--trials',type=int,default=100);p.add_argument('--draws',type=int,default=40)
 p.add_argument('--calibration-trials',type=int,default=100)
 a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 import shutil
 for file in ('evaluate_transformed.py','draper/transformed.py','draper/model.py'):
  shutil.copyfile(file,a.output/Path(file).name)
 blocks=[{'id':i,'calls':[],'instructions':2,'successor_edges':s} for i,s in enumerate(([1,2],[3],[4],[]))]
 edges=[{'src':s,'dst':t,'counter':c} for s,t,c in ((-1,0,0),(0,1,1),(0,2,2),(1,3,1),(2,3,2),(3,-1,3))]
 graph={'ir_id':'coverage-diamond','functions':[{'name':'main','closed_calls':False,'blocks':blocks,'edges':edges}]}
 system=FlowSystem(graph);truth=np.array([10000,8000,2000,8000,2000,10000.]);initial=np.array([10000,5000,5000,5000,5000,10000.])
 base=[relation('identity',[0]),relation('inline',[1],[.5]),relation('clone',[2],[.25]),relation('merge-exclusive',[1,2]),relation('loop',[3],[.25])]
 records=[]
 for case in ('constrained','ambiguous'):
  rows=base if case=='constrained' else base[:2]
  alternate=[list(r) for r in rows];alternate[1]=relation('inline',[1],[1.])
  scenarios=[{'prior':.5,'operator_rows':rows},{'prior':.5,'operator_rows':alternate}]
  for divisor in (16,64,256):
   calibration=[]
   for trial in range(a.calibration_trials):
    random=np.random.default_rng(2000042+trial);rate=1/divisor
    expected=observation_operator(system,{'operator_rows':rows})@truth
    observed={'ir_id':graph['ir_id'],'samples':random.poisson(rate*expected).tolist(),'present':[True]*len(rows),'rate':rate}
    result=intervals(system,observed,initial,scenarios,repetitions=a.draws,seed=3000042+trial)
    calibration.append(result['block_intervals'])
   multiplier=calibrate_width(calibration,np.tile(system.blocks@truth,(len(calibration),1)))
   for trial in range(a.trials):
    random=np.random.default_rng(42+trial)
    rate=1/divisor
    expected=observation_operator(system,{'operator_rows':rows})@truth
    samples=random.poisson(rate*expected)
    observed={'ir_id':graph['ir_id'],'samples':samples.tolist(),'present':[True]*len(rows),'rate':rate}
    result=intervals(system,observed,initial,scenarios,repetitions=a.draws,seed=1000042+trial)
    records.append({'case':case,'divisor':divisor,'trial':trial,
       **coverage(result['block_intervals'],system.blocks@truth),
       **mapping_score(result['mapping_probabilities'],0),'correct_mapping_probability':float(result['mapping_probabilities'][0]),
       'calibration_multiplier':multiplier,'calibrated':coverage(widen(result['block_intervals'],multiplier),system.blocks@truth)})
   save(a.output/'trials.json',records)
   print(case,divisor,'finished',flush=True)
 groups=[]
 for case in ('constrained','ambiguous'):
  for divisor in (16,64,256):
   subset=[r for r in records if r['case']==case and r['divisor']==divisor]
   values=np.array([r['coverage'] for r in subset]);random=np.random.default_rng(42)
   ci=np.quantile(random.choice(values,size=(2000,len(values)),replace=True).mean(axis=1),[.025,.975])
   groups.append({'case':case,'rate':1/divisor,'trials':len(subset),'coverage':float(values.mean()),'coverage_ci95':ci.tolist(),
      'calibrated_coverage':float(np.mean([r['calibrated']['coverage'] for r in subset])),
      'calibrated_mean_width':float(np.mean([r['calibrated']['mean_width'] for r in subset])),
      'calibration_multiplier':subset[0]['calibration_multiplier'],
      **{key:float(np.mean([r[key] for r in subset])) for key in ('mean_width','brier','log_loss','top_one_correct','correct_mapping_probability')}})
 save(a.output/'summary.json',{'status':'complete','kind':'controlled synthetic transformation experiment; no compiler application claim',
     'nominal_interval':.95,'draws':a.draws,'calibration_trials':a.calibration_trials,'independent_calibration_and_test_seeds':True,'groups':groups})

if __name__=='__main__':main()
