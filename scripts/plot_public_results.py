"""绘制已完成实验中计数改善、运行变慢的案例，保留原始区间。"""
import argparse,json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('summary',type=Path)
    parser.add_argument('--suite',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    rows=[r for r in json.loads(args.summary.read_text())['programs']
          if r['suite']==args.suite and r.get('count_improved_but_significantly_slower')]
    if not rows:raise SystemExit('No measured counterexamples in this suite')
    rows.sort(key=lambda r:r['speedup_percent'])
    y=np.arange(len(rows))
    gains=[100*(1-r['final_count_error']/r['profi_count_error']) for r in rows]
    speed=np.array([r['speedup_percent'] for r in rows])
    bounds=np.array([r['ci95_percent'] for r in rows]).T
    fig,axes=plt.subplots(1,2,figsize=(10,max(3,len(rows)*.48+1.3)),sharey=True,
                         gridspec_kw={'width_ratios':[1,1.25]})
    axes[0].barh(y,gains,color='#287e8e',height=.58)
    axes[0].set_yticks(y,[r['program'] for r in rows])
    axes[0].set_xlabel('Reduction in block-count error (%)')
    axes[0].set_xlim(0,max(gains)*1.22)
    for i,gain in enumerate(gains):axes[0].text(gain+.6,i,f'{gain:.1f}%',va='center',fontsize=9)
    axes[1].errorbar(speed,y,xerr=np.maximum(np.vstack((speed-bounds[0],bounds[1]-speed)),0),
                     fmt='o',color='#b24637',capsize=3)
    axes[1].axvline(0,color='#888888',linewidth=1)
    axes[1].set_xlabel('Runtime speedup vs LLVM profi (%)\nPaired 95% bootstrap interval')
    for ax in axes:
        ax.grid(axis='x',alpha=.2);ax.set_axisbelow(True)
        ax.spines[['top','right']].set_visible(False)
    axes[0].invert_yaxis()
    fig.suptitle('More accurate counts can still produce slower programs',fontsize=13)
    fig.tight_layout()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(args.output.with_suffix('.png'),dpi=180)
    fig.savefig(args.output.with_suffix('.pdf'))
    args.output.with_suffix('.json').write_text(json.dumps({'suite':args.suite,'programs':rows},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
