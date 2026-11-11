"""新增套件执行新算法前，合并重复应用家族并沿用已有开发/评价划分。"""
import argparse
import json
from pathlib import Path
from run_draper import save

ALIASES={
    'MultiSource/Benchmarks/mediabench/gsm':'MultiSource/Benchmarks/MiBench/telecomm-gsm',
    'MultiSource/Benchmarks/mediabench/jpeg':'MultiSource/Benchmarks/MiBench/consumer-jpeg',
}


def main():
    p=argparse.ArgumentParser();p.add_argument('corpus',type=Path);p.add_argument('existing_index',type=Path);a=p.parse_args()
    existing=json.loads(a.existing_index.read_text());splits={}
    for row in existing['programs']:
        family=row['family']
        if family in splits and splits[family]!=row['split']:raise ValueError('Existing family has inconsistent splits')
        splits[family]=row['split']
    if (a.corpus/'paired-records-v2').exists():raise ValueError('Apply family assignments before creating the paired index')
    changes=[]
    for filename in ('summary.json','configuration.json'):
        path=a.corpus/filename;value=json.loads(path.read_text())
        backup=a.corpus/(filename+'.before-family-alignment')
        if backup.exists():raise FileExistsError(backup)
        backup.write_bytes(path.read_bytes())
        for row in value['programs']:
            family=ALIASES.get(row['family'],row['family']);split=splits.get(family,row['split'])
            if (family,split)!=(row['family'],row['split']):
                changes.append({'file':filename,'test':row['test'],'old_family':row['family'],'family':family,'old_split':row['split'],'split':split})
            row.update(family=family,split=split)
        value['family_assignment']='Repeated application families inherit the existing corpus split before algorithm experiments'
        save(path,value)
    save(a.corpus/'family-alignment.json',{'existing_index':str(a.existing_index),'changes':changes})
    print(json.dumps(changes,indent=2))


if __name__=='__main__':main()
