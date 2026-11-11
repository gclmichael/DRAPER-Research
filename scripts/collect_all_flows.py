"""逐项采集全部工作负载；失败单独记录，不删掉困难程序。"""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
from run_draper import save

def main():
 p=argparse.ArgumentParser();p.add_argument('corpus',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--bridge',type=Path,required=True)
 p.add_argument('--programs',nargs='+');p.add_argument('--nonlocal-flow',action='store_true')
 a=p.parse_args();a.output.mkdir(exist_ok=False,parents=True)
 records=json.loads((a.corpus/'paired-records-v2/summary.json').read_text())['programs']
 if a.programs:records=[r for r in records if Path(r['test']).stem in a.programs]
 # 采集所有集合，算法调参只使用开发集合。
 records.sort(key=lambda r:r['source_lines'])
 results=[]
 for r in records:
  name=Path(r['test']).stem;start=time.monotonic()
  cmd=[sys.executable,'prepare_large_experiment.py',str(a.corpus),'--bridge',str(a.bridge),'--output',str(a.output/name),'--program',name,'--evaluate-heldout']
  if a.nonlocal_flow:cmd.append('--nonlocal-flow')
  print('Collecting',name,flush=True)
  with (a.output/(name+'.log')).open('w') as log:
   try:
    result=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=3600)
    status='complete' if result.returncode==0 else 'failed'
   except subprocess.TimeoutExpired:status='timeout'
  results.append({'program':name,'test':r['test'],'split':r['split'],'status':status,'seconds':time.monotonic()-start})
  save(a.output/'summary.json',{'status':'running' if len(results)<len(records) else 'finished','programs':results,'total':len(records)})
  print(name,status,flush=True)

if __name__=='__main__':main()
