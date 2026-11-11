"""在保存的独立评价抽样上比较逐块覆盖与整次运行覆盖，并报告区间宽度。"""
import argparse,json
from pathlib import Path
import numpy as np
from draper.transformed import widen
from run_draper import save

def main():
 p=argparse.ArgumentParser();p.add_argument('experiment',type=Path);a=p.parse_args()
 c=np.load(a.experiment/'calibration.npz');e=np.load(a.experiment/'evaluation.npz');s=json.loads((a.experiment/'summary.json').read_text())
 b=c['bounds'];minimum=s['min_half_width'];scores=np.abs(c['truth']-b.mean(axis=2))/np.maximum((b[:,:,1]-b[:,:,0])/2,minimum)
 marginal=max(1.,float(np.quantile(scores,.95,method='higher')))
 records=[]
 for label,multiplier in [('uncalibrated',None),('marginal',marginal),('simultaneous',s['multiplier'])]:
  bounds=e['bounds'] if multiplier is None else np.array([widen(v,multiplier,minimum) for v in e['bounds']])
  covered=(bounds[:,:,0]<=e['truth'])&(e['truth']<=bounds[:,:,1]);per_trial=covered.mean(axis=1)
  ci=np.quantile(np.random.default_rng(42).choice(per_trial,(2000,len(per_trial)),replace=True).mean(axis=1),[.025,.975])
  width=bounds[:,:,1]-bounds[:,:,0]
  records.append({'target':label,'multiplier':multiplier,'marginal_coverage':float(per_trial.mean()),'coverage_ci95':ci.tolist(),
    'simultaneous_coverage':float(covered.all(axis=1).mean()),'mean_width':float(width.mean()),
    'width_sum_over_count_sum':float(width.sum(axis=1).mean()/e['truth'].sum())})
 save(a.experiment/'coverage-comparison.json',{'calibration':'marginal uses empirical calibration quantile; no block independence assumed in evaluation, which resamples whole runs',
  'calibration_trials':len(c['bounds']),'evaluation_trials':len(e['bounds']),'results':records})
 print(json.dumps(records,indent=2))

if __name__=='__main__':main()
