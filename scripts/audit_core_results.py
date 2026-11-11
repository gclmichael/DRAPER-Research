"""在算法输出和计时固定后，汇总相对 profi 的计数误差与性能反例。"""
import argparse
import json
from pathlib import Path
import numpy as np
from draper.model import FlowSystem,evaluate
from draper.transformed import coverage
from run_draper import save


def main():
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('experiment',type=Path)
    p.add_argument('--timing',type=Path);p.add_argument('--output',type=Path)
    a=p.parse_args();root=a.experiment
    summary=json.loads((root/'summary.json').read_text())
    timing=json.loads(((a.timing or root/'timing')/'summary.json').read_text())
    if summary['status']!='complete' or timing['status']!='complete':raise ValueError('Experiment is incomplete')
    rows=[]
    for program in summary['programs']:
        name=program['program'];old=a.source/name;new=root/name
        system=FlowSystem(json.loads((new/'graph.json').read_text()))
        exact=json.loads((old/'exact-0.json').read_text())
        baseline=evaluate(system,exact,system.vector(json.loads((old/'profi.json').read_text())))
        output=system.vector(json.loads((new/'risk-projected.json').read_text()))
        calibrated=system.vector(json.loads((new/'calibrated.json').read_text()))
        final=evaluate(system,exact,output)
        measured=next(v for v in timing['programs'] if v['program']==name)['comparisons']['risk-projected']['vs_profi']
        change=float(np.abs(output-calibrated).sum());budget=.01*max(float(calibrated.sum()),1)
        if change>budget+1e-7*max(float(calibrated.sum()),1):raise ValueError('Final output exceeds modification budget')
        anchor=json.loads((new/'anchors.json').read_text())['anchors']
        block_counts=system.blocks@output
        errors=[abs(float(block_counts[v['block']])-v['count']) for v in anchor]
        rows.append({'program':name,'profi_count_error':baseline['block_relative_l1'],
                     'final_count_error':final['block_relative_l1'],'timing_vs_profi':measured,
                     'count_improved_but_significantly_slower':final['block_relative_l1']<baseline['block_relative_l1'] and measured['ci95_percent'][1]<0,
                     'final_change':change,'budget':budget,'final_flow_audit':system.audit(output),
                     'maximum_measured_anchor_absolute_error':max(errors,default=0.),
                     'projection_accepted':program['compiler_replay_accepted']})
        if (new/'block-uncertainty.npz').exists():
            with np.load(new/'block-uncertainty.npz') as stored:
                rows[-1]['calibrated_interval_coverage']=coverage(stored['bounds'],system.blocks@system.vector(exact))
    save(a.output or root/'final-audit.json',{'programs':rows,'counterexamples':[r['program'] for r in rows if r['count_improved_but_significantly_slower']]})
    print(json.dumps(rows,indent=2))


if __name__=='__main__':main()
