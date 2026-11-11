"""实际优化后计数的独立泊松抽样重复实验；校准与评价使用不同随机种子。"""
import argparse,json,os,shutil
from pathlib import Path
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
import numpy as np
from draper.model import FlowSystem
from draper.transformed import intervals,coverage,calibrate_width,widen
from run_draper import save

def main():
 p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--output',type=Path,required=True)
 p.add_argument('--trials',type=int,default=100);p.add_argument('--draws',type=int,default=40)
 p.add_argument('--calibration-file',type=Path);a=p.parse_args()
 a.output.mkdir(parents=True,exist_ok=False)
 for name in ('evaluate_compiled_intervals.py','draper/model.py','draper/transformed.py'):shutil.copyfile(name,a.output/Path(name).name)
 def read(name):return json.loads((a.input/name).read_text())
 system=FlowSystem(read('graph.json'));truth=system.blocks@system.vector(read('exact-0.json'));initial=system.vector(read('initial.json'))
 scenarios=read('ratio-scenarios.json');observed=read('observations.json')
 measured=read('run-1.json')['occurrences'];selected=[i for r in read('ratios.json') for i in r['occurrences']]
 counts=np.array([measured[i]['count'] for i in selected],float);rate=observed['rate'];calibration=[];rows=[];evaluation_bounds=[]
 min_half_width=1/rate
 if a.calibration_file:
  prior=np.load(a.calibration_file);np.testing.assert_array_equal(prior['truth'],truth);calibration=list(prior['bounds'])
  multiplier=calibrate_width(calibration,np.tile(truth,(len(calibration),1)),min_half_width=min_half_width)
 phases=[('evaluation',4000042)] if a.calibration_file else [('calibration',2000042),('evaluation',4000042)]
 for phase,start in phases:
  for trial in range(a.trials):
   samples=np.random.default_rng(start+trial).poisson(rate*counts)
   data={**observed,'samples':samples.tolist()}
   try:fitted=intervals(system,data,initial,scenarios,repetitions=a.draws,seed=start+1000000+trial)
   except Exception:
    save(a.output/'failure.json',{'phase':phase,'trial':trial,'observations':data});raise
   if phase=='calibration':calibration.append(fitted['block_intervals'])
   else:
    evaluation_bounds.append(fitted['block_intervals'])
    rows.append({'trial':trial,'unidentified_blocks':fitted['unidentified_blocks'],
      'raw':coverage(fitted['block_intervals'],truth),'calibrated':coverage(widen(fitted['block_intervals'],multiplier,min_half_width),truth),
      'mapping_probabilities':fitted['mapping_probabilities'].tolist()})
    save(a.output/'trials.json',rows)
   if (trial+1)%10==0:print(phase,trial+1,flush=True)
  if phase=='calibration':
   multiplier=calibrate_width(calibration,np.tile(truth,(a.trials,1)),min_half_width=min_half_width)
   np.savez_compressed(a.output/'calibration.npz',bounds=np.asarray(calibration),truth=truth)
  else:np.savez_compressed(a.output/'evaluation.npz',bounds=np.asarray(evaluation_bounds),truth=truth)
 save(a.output/'calibration.json',{'multiplier':multiplier,'trials':len(calibration),'min_half_width':min_half_width,
  'source':str(a.calibration_file) if a.calibration_file else 'calibration.npz'})
 summary={'status':'complete','kind':'independent Poisson thinning of actual optimized instrumented counts; fixed program and input',
  'trials_per_phase':a.trials,'draws':a.draws,'rate':rate,'multiplier':multiplier,'min_half_width':min_half_width,
  'calibration_uses_early_counts':'independent calibration replicates only; evaluation truth never passed to fitting'}
 for kind in ('raw','calibrated'):
  summary[kind]={key:float(np.mean([r[kind][key] for r in rows])) if all(r[kind][key] is not None for r in rows) else None
    for key in ('coverage','positive_coverage','mean_width','unbounded_fraction','finite_coverage')}
 save(a.output/'summary.json',summary)
 print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__':main()
