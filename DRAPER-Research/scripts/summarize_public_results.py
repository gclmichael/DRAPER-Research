"""按实际完成状态汇总公开套件，禁止把仅构建或旧计时算成算法性能结果。"""
import argparse,json,math
from pathlib import Path
import numpy as np
from run_draper import save
from measure_runtime import paired_improvement

def read(path):
 return json.loads(path.read_text()) if path.exists() else {}

def main():
 p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data'));p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 suites=[];rows=[]
 for label,collections,cores in [
  ('LLVM 多源程序：早期计数对照',['all-flows-v2','nonlocal-flows-v1'],['all-core-v2','nonlocal-core-v1']),
  ('LLVM 多源程序：优化后观测',['all-flows-v2','nonlocal-flows-v1'],['all-transformed-core-v1','all-transformed-core-v1']),
  ('PolyBench',['polybench-flows-v1'],['polybench-core-v1']),
  ('现代 SQLite、zstd、FFmpeg',['teacher-app-flows-v1'],['teacher-app-core-v1']),
  ('Rodinia / MediaBench（LLVM 子集）',['rodinia-mediabench-flows-v1'],['rodinia-mediabench-core-v1'])]:
  records={}
  for collection,core in zip(collections,cores):
   for entry in read(a.data/collection/'summary.json').get('programs',[]):
    previous=records.get(entry['program'],{})
    if entry['status']=='complete' or not previous:
     records[entry['program']]={**entry,'collection':collection,'core':core,'collected':entry['status']=='complete'}
  for name,entry in records.items():
   root=a.data/entry['core']/name;algorithm=read(root/'summary.json')
   row={'suite':label,'program':name,'split':entry.get('split'),'collected':entry['collected'],'algorithm_complete':algorithm.get('status')=='complete','timing_complete':False}
   batch=read(a.data/entry['core']/'summary.json')
   recorded=next((v for v in batch.get('programs',[]) if v['program']==name),{})
   row['batch_status']=recorded.get('status','pending')
   if 'failed_step' in recorded:row['failed_step']=recorded['failed_step']
   observation=read(a.data/entry['core']/(name+'-observations')/'summary.json')
   if observation.get('status')=='complete':
    row['optimized_observation']={k:observation.get(k) for k in ('observation_rows','mapped_blocks','unknown_occurrences',
     'mapping_alternatives','exclusive_merge_candidates','mean_count_compatible_mapping_probability',
     'coverage_all_blocks','coverage_mapped_blocks','measurement_runs','count_conservation_failures')}
    row['optimized_observation']['unidentified_block_count']=len(observation.get('unidentified_blocks',[]))
   timing_path=root/('timing-corrected' if entry['core']=='all-core-v2' else 'timing')
   timing=read(timing_path/'summary.json')
   if row['algorithm_complete'] and timing.get('status')=='complete':
    measured=next(v for v in timing['programs'] if v['program']==name);report=next(v for v in algorithm['programs'] if v['program']==name)
    row.update(timing_complete=True,**measured['comparisons']['risk-projected']['vs_profi'])
    row['significantly_slower']=row['ci95_percent'][1]<0
    row['significantly_slower_over_one_percent']=row['significantly_slower'] and row['speedup_percent'] < -1
    row['projection_vs_calibrated']=paired_improvement(measured['seconds_per_execution']['calibrated'],measured['seconds_per_execution']['risk-projected'])
    row['calibrated_timing_vs_profi']=measured['comparisons']['calibrated']['vs_profi']
    row['calibrated_count_error']=report['calibrated']['block_relative_l1']
    audit=read(root/('final-audit-corrected.json' if entry['core']=='all-core-v2' else 'final-audit.json'))
    if audit:
     auditrow=next(v for v in audit['programs'] if v['program']==name)
     row.update(profi_count_error=auditrow['profi_count_error'],final_count_error=auditrow['final_count_error'],
      count_improved_but_significantly_slower=auditrow['count_improved_but_significantly_slower'])
     row['count_improved_but_slower_over_one_percent']=row['count_improved_but_significantly_slower'] and row['speedup_percent'] < -1
     row['calibrated_count_improved_but_significantly_slower']=(row['calibrated_count_error']<row['profi_count_error']
        and row['calibrated_timing_vs_profi']['ci95_percent'][1]<0)
     if 'calibrated_interval_coverage' in auditrow:row['calibrated_interval_coverage']=auditrow['calibrated_interval_coverage']
    row['projection_accepted']=report['compiler_replay_accepted']
    row['final_strategy']=report.get('final_strategy','proposed' if row['projection_accepted'] else 'center')
   rows.append(row)
  subset=[r for r in rows if r['suite']==label];timed=[r for r in subset if r['timing_complete']]
  suites.append({'suite':label,'listed':len(subset),'collected':sum(r['collected'] for r in subset),
   'algorithm_complete':sum(r['algorithm_complete'] for r in subset),'timing_complete':len(timed),
   'geometric_mean_speedup_percent':100*(math.exp(np.mean([math.log1p(r['speedup_percent']/100) for r in timed]))-1) if timed else None,
   'significantly_slower':sum(r['significantly_slower'] for r in timed),
   'significantly_slower_over_one_percent':sum(r['significantly_slower_over_one_percent'] for r in timed),
   'count_improved_but_significantly_slower':sum(r.get('count_improved_but_significantly_slower',False) for r in timed),
   'calibrated_count_improved_but_significantly_slower':sum(r.get('calibrated_count_improved_but_significantly_slower',False) for r in timed),
   'projection_geometric_mean_speedup_percent':100*(math.exp(np.mean([math.log1p(r['projection_vs_calibrated']['speedup_percent']/100) for r in timed]))-1) if timed else None,
   'count_improved_but_slower_over_one_percent':sum(r.get('count_improved_but_slower_over_one_percent',False) for r in timed)})
 a.output.mkdir(parents=True,exist_ok=True);save(a.output/'summary.json',{'suites':suites,'programs':rows,'spec':'User deferred SPEC CPU until licensed materials are available'})
 lines=['# 公开套件实验进度与结果','','SPEC CPU 2017 按用户安排暂不运行。原 40 项的旧计时受到子进程退出轮询影响，本表只计入修正后的重复计时。',
  '','| 套件 | 已列入 | 已采集 | 完成算法 | 有效计时 |','|---|---:|---:|---:|---:|']
 for s in suites:lines.append(f'| {s["suite"]} | {s["listed"]} | {s["collected"]} | {s["algorithm_complete"]} | {s["timing_complete"]} |')
 lines+=['','算法完成包括恢复、编译决策回放和输出正确性检查。已采集表示早期全块计数和流约束检查成功。尚未开始构建的套件不会预先计入程序数量。LLVM 多源程序的两行使用同一组 40 项工作负载，不能相加为 80 个程序。PolyBench 和现代应用使用优化后观测。',
  '早期计数对照中，原 38 项使用保留的旧实现，anagram、Lua 使用修复非局部返回后的实现；40 项合计用于记录覆盖情况，统一新版方法的效果由优化后观测一行评价。',
  '','| 套件与方法 | 有效计时 | 几何平均加速 | 显著变慢 | 计数改善且显著变慢 | 其中下降超过 1% |',
  '|---|---:|---:|---:|---:|---:|']
 for s in suites:
  speed=f'{s["geometric_mean_speedup_percent"]:+.3f}%' if s['timing_complete'] else '—'
  lines.append(f'| {s["suite"]} | {s["timing_complete"]} | {speed} | {s["significantly_slower"]} | {s["count_improved_but_significantly_slower"]} | {s["count_improved_but_slower_over_one_percent"]} |')
 lines+=['','| 同一观测下的风险修正对照 | 修正前的计数改善却变慢案例 | 修正后的案例 | 修正相对校准结果的几何平均加速 |',
  '|---|---:|---:|---:|']
 for s in suites:
  speed=f'{s["projection_geometric_mean_speedup_percent"]:+.3f}%' if s['timing_complete'] else '—'
  lines.append(f'| {s["suite"]} | {s["calibrated_count_improved_but_significantly_slower"]} | {s["count_improved_but_significantly_slower"]} | {speed} |')
 lines+=['','| 套件与方法 | 程序 | 集合 | 状态 | 相对 LLVM profi 的加速 | 95% 区间 |','|---|---|---|---|---:|---|']
 for r in rows:
  state='已计时' if r['timing_complete'] else '算法完成，待有效计时' if r['algorithm_complete'] else '已采集，待算法实验' if r['collected'] else '采集待修复'
  if r['batch_status'] in ('failed','timeout'):state=f'第 {r["failed_step"]+1} 步'+('超时' if r['batch_status']=='timeout' else '失败')+'，待修复'
  point=f'{r["speedup_percent"]:+.3f}%' if r['timing_complete'] else '—'
  interval=f'[{r["ci95_percent"][0]:+.3f}%, {r["ci95_percent"][1]:+.3f}%]' if r['timing_complete'] else '—'
  lines.append(f'| {r["suite"]} | {r["program"]} | {r["split"] or "待划分"} | {state} | {point} | {interval} |')
 lines+=['','正值表示加速。每项 15 轮交错计时，短程序在每轮启动多个独立进程。计时包含进程启动，输出验证在计时外执行。',
  '“计数改善却性能下降”同时要求块计数相对误差降低、加速区间上限小于零；另列下降超过 1% 的数量。']
 lines+=['','| 优化后观测的程序 | 全部块覆盖率 | 实际执行块覆盖率 | 补充计数后实际执行块覆盖率 | 无有限上界的比例 | 映射候选位置 | 不可辨识块 | 计数相容候选概率 |',
  '|---|---:|---:|---:|---:|---:|---:|---:|']
 for r in rows:
  if 'optimized_observation' not in r:continue
  o=r['optimized_observation'];prob=o['mean_count_compatible_mapping_probability']
  probability=f'{prob:.3f}' if prob is not None else '无候选位置'
  positive=o['coverage_all_blocks']['positive_coverage'];positive_text=f'{positive:.2%}' if positive is not None else '无执行块'
  calibrated=r.get('calibrated_interval_coverage',{}).get('positive_coverage');calibrated_text=f'{calibrated:.2%}' if calibrated is not None else '待完成'
  lines.append(f'| {r["program"]} | {o["coverage_all_blocks"]["coverage"]:.2%} | {positive_text} | {calibrated_text} | {o["coverage_all_blocks"]["unbounded_fraction"]:.3%} | {o["mapping_alternatives"]} | {o["unidentified_block_count"]} | {probability} |')
 lines+=['','本表的覆盖率来自每个程序一次模拟采样；100 次独立抽样的覆盖率实验另见 OBSERVATION_MODEL_2026-11-10.md。计数相容候选概率衡量候选预测次数与参考次数的相容程度，不能等同于真实映射识别准确率。比例校准使用全探针插桩，其耗时单独保存在 JSON 中。']
 (a.output/'results.md').write_text('\n'.join(lines)+'\n')
 print(json.dumps(suites,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
