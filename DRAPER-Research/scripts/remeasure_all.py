"""重测既有二进制，修正超时轮询造成的计时台阶；保留旧结果供检查。"""
import argparse,json,subprocess,sys
from pathlib import Path
from run_draper import save

def main():
 p=argparse.ArgumentParser();p.add_argument('collection',type=Path);p.add_argument('core',type=Path);a=p.parse_args()
 results=[]
 for entry in json.loads((a.core/'summary.json').read_text())['programs']:
  if entry['status']!='complete':continue
  name=entry['program'];root=a.core/name;row={'program':name,'status':'complete'}
  commands=[[sys.executable,'measure_runtime.py',str(root),'--variants','calibrated','risk-projected','--rounds','15','--seconds','.3',
    '--output',str(root/'timing-corrected'),'--reuse-binaries',str(root/'timing')],
   [sys.executable,'audit_core_results.py',str(a.collection/name),str(root),'--timing',str(root/'timing-corrected'),'--output',str(root/'final-audit-corrected.json')]]
  with (root/'remeasure.log').open('w') as log:
   for command in commands:
    try:
     result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=1800)
     if result.returncode:row['status']='failed';break
    except subprocess.TimeoutExpired:row['status']='timeout';break
  results.append(row);save(a.core/'remeasure-summary.json',{'status':'running','programs':results})
  print(name,row['status'],flush=True)
 save(a.core/'remeasure-summary.json',{'status':'complete','programs':results})

if __name__=='__main__':main()
