"""统一公开工作负载的全块数据入口，保留套件来源、输入划分和实际完成状态。"""
import argparse,json
from collections import Counter
from pathlib import Path
from run_draper import save


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,default=Path('data'))
    parser.add_argument('--output',required=True,type=Path);args=parser.parse_args();data=args.data.resolve()
    records=[]
    suites=[
        ('LLVM MultiSource','industrial-corpus-v1',['all-flows-v2','nonlocal-flows-v1']),
        ('PolyBench','polybench-corpus-v1',['polybench-flows-v1']),
        ('Modern applications','teacher-apps-v1',['teacher-app-flows-v1'])]
    extra=data/'rodinia-mediabench-flows-v1/summary.json'
    if extra.exists():
        if json.loads(extra.read_text())['status']!='finished':raise ValueError('Additional public suite collection is still running')
        suites.append(('Rodinia / MediaBench (LLVM subset)','rodinia-mediabench-corpus-v1',['rodinia-mediabench-flows-v1']))
    for suite,corpus,collections in suites:
        source=json.loads((data/corpus/'summary.json').read_text())
        source_records={row.get('test',row.get('program')):row for row in source['programs']}
        collected={}
        for collection in collections:
            for row in json.loads((data/collection/'summary.json').read_text())['programs']:
                if row['program'] not in collected or row['status']=='complete':
                    collected[row['program']]=(row,data/collection/row['program'])
        for name,(row,folder) in collected.items():
            if row['status']!='complete':raise ValueError(f'Incomplete full-flow collection: {suite}/{name}')
            summary=json.loads((folder/'summary.json').read_text())
            if summary['status']!='complete':raise ValueError(f'Incomplete experiment: {folder}')
            actual=next(x for x in summary['programs'] if x['program']==name)
            files={key:str(folder/name/file) for key,file in {
                'canonical_ir':'canonical.ll','graph':'graph.json','exact_run_0':'exact-0.json',
                'exact_run_1':'exact-1.json','simulated_observations':'observations.json','initial_profile':'profi.json'}.items()}
            for file in files.values():
                if not Path(file).is_file():raise FileNotFoundError(file)
            original=source_records[row.get('test',name)]
            family='sqlite' if name in ('sqlite3','sqlite-modern') else original.get('family',name)
            execution=dict(actual['execution']);evaluation=execution.pop('evaluation',None)
            kernel=suite=='PolyBench' or '/Rodinia/' in row.get('test','')
            records.append({'program':name,'suite':suite,'family':family,'kind':'loop kernel' if kernel else 'application',
                'split':row['split'],'source_lines':actual['source_lines'],
                'functions':actual['functions'],'blocks':actual['blocks'],'edges':actual['edges'],
                'exact_flow_audit':actual['exact_audit'],'repeated_counts_equal':actual['counts_repeat_equal'],
                'collection_execution':execution,'independent_evaluation_execution':evaluation,
                'files':files,'flow_directory':str(folder),'source_record':str(data/corpus/'summary.json'),
                'historical_performance_viewed':suite=='LLVM MultiSource','used_for_interval_calibration':name=='aha'})
    if len({r['program'] for r in records})!=len(records):raise ValueError('Program names must remain unique')
    family_splits={}
    for row in records:
        if row['family'] in family_splits and family_splits[row['family']]!=row['split']:
            raise ValueError(f'Family crosses input split: {row["family"]}')
        family_splits[row['family']]=row['split']
    summary={'status':'complete','workloads':len(records),'application_workloads':sum(r['kind']=='application' for r in records),
        'application_families':len({r['family'] for r in records if r['kind']=='application'}),
        'loop_kernels':sum(r['kind']=='loop kernel' for r in records),'splits':dict(Counter(r['split'] for r in records)),
        'independent_evaluation_inputs':sum(r['independent_evaluation_execution'] is not None for r in records),
        'observation_kind':'Simulated sparse observations derived from measured LLVM full-block counts; optimized-probe experiments reported separately',
        'programs':records}
    args.output.mkdir(parents=True,exist_ok=True);save(args.output/'index.json',summary)
    lines=['# 公开工作负载全块数据索引','',
        f'共 {summary["workloads"]} 个工作负载，其中 {summary["application_workloads"]} 个应用工作负载来自 {summary["application_families"]} 个应用家族，另有 {summary["loop_kernels"]} 个循环内核。',
        '','此索引表示两次精确计数及其模拟稀疏观测已完成；新算法性能结果另见 ../public-results-v1/results.md。',
        '现代三个应用另有独立性能评价输入。原 40 项的旧算法性能已查看，aha 已用于区间校准，相关字段保留在 JSON 中。',
        '','| 程序 | 来源 | 集合 | 编译源码行数 | 函数 | 基本块 |','|---|---|---|---:|---:|---:|']
    for row in records:lines.append(f'| {row["program"]} | {row["suite"]} | {row["split"]} | {row["source_lines"]:,} | {row["functions"]:,} | {row["blocks"]:,} |')
    lines+=['','index.json 中的路径指向 node3 的工作目录。模拟抽样和重复运行不会增加独立程序数量；源码行数包含生成代码，不等于实际执行代码量。']
    (args.output/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='programs'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
