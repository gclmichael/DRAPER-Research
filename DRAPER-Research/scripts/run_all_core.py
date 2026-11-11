"""使用已冻结的实现，对成功采集的全部程序运行恢复、计时与反例审计。"""
import argparse,json,subprocess,sys,time
from pathlib import Path
from run_draper import save

def main():
 p=argparse.ArgumentParser();p.add_argument('collection',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--bridge',type=Path,required=True)
 p.add_argument('--transformed',action='store_true',help='Measure optimized probe occurrences and infer mapping/ratio scenarios before compiler replay')
 a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 records=json.loads((a.collection/'summary.json').read_text())['programs'];results=[]
 for entry in records:
  name=entry['program'];row={**entry,'status':'not-collected'}
  if entry['status']=='complete':
   start=time.monotonic();source=a.collection/name;out=a.output/name
   commands=[]
   if a.transformed:
    transformed=a.output/(name+'-observations')
    commands.append([sys.executable,'collect_transformed_observations.py',str(source),'--program',name,
      '--output',str(transformed),'--bridge',str(a.bridge)])
    source=transformed/'experiment'
   row['observation_model']='optimized probe ratios and mapping candidates' if a.transformed else 'identity at early intermediate representation'
   commands += [[sys.executable,'run_core_risk.py',str(source),'--output',str(out),'--bridge',str(a.bridge),'--fresh-calibration'],
      [sys.executable,'measure_runtime.py',str(out),'--variants','calibrated','risk-projected','--rounds','15','--seconds','.3'],
      [sys.executable,'audit_core_results.py',str(source),str(out)]]
   row['status']='complete'
   for step,cmd in enumerate(commands):
    print(name,'step',step,flush=True)
    with (a.output/f'{name}-{step}.log').open('w') as log:
     try:
      result=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=14400)
      if result.returncode:row['status']='failed';row['failed_step']=step;break
     except subprocess.TimeoutExpired:row['status']='timeout';row['failed_step']=step;break
   row['seconds']=time.monotonic()-start
  results.append(row)
  save(a.output/'summary.json',{'status':'running' if len(results)<len(records) else 'finished','total':len(records),'programs':results})
  print(name,row['status'],flush=True)

if __name__=='__main__':main()
