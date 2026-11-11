"""逐项构建并运行老师列出的三个现代应用，失败项保留完整日志。"""
import argparse,json,subprocess,sys,time
from pathlib import Path
from run_draper import save


def main():
 p=argparse.ArgumentParser();p.add_argument('--build',required=True,type=Path);p.add_argument('--flows',required=True,type=Path)
 p.add_argument('--core',required=True,type=Path);p.add_argument('--bridge',required=True,type=Path);a=p.parse_args()
 finished=a.core/'summary.json'
 if finished.exists():
  previous=json.loads(finished.read_text())
  if previous['status']=='finished' and len(previous['programs'])==3 and all(r['status']=='complete' for r in previous['programs']):
   print('All three modern applications already completed',flush=True);return
 if not a.build.exists():subprocess.run([sys.executable,'build_teacher_apps.py','--output',str(a.build)],check=True)
 records=json.loads((a.build/'summary.json').read_text())['programs'];a.flows.mkdir(parents=True,exist_ok=False);results=[]
 for entry in records:
  name=entry['program'];row={'program':name,'status':'not-built','split':entry['split'],'source_lines':entry.get('source_lines',0)}
  if entry['status']=='complete':
   start=time.monotonic()
   command=[sys.executable,'prepare_large_experiment.py',str(a.build/name),'--application-config',str(a.build/name/'application.json'),
    '--program',name,'--output',str(a.flows/name),'--bridge',str(a.bridge),'--nonlocal-flow']
   with (a.flows/(name+'.log')).open('w') as log:
    try:
     r=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=7200)
     row['status']='complete' if not r.returncode else 'failed'
    except subprocess.TimeoutExpired:row['status']='timeout'
   row['seconds']=time.monotonic()-start
  results.append(row);save(a.flows/'summary.json',{'status':'finished' if len(results)==len(records) else 'running','total':len(records),'programs':results})
  print(name,row['status'],flush=True)
 subprocess.run([sys.executable,'run_all_core.py',str(a.flows),'--output',str(a.core),'--bridge',str(a.bridge),'--transformed'],check=True)


if __name__=='__main__':main()
