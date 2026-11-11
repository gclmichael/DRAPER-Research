"""构建官方多源程序语料：源码、输入、编译命令、中间表示、两次插桩计数与检查结果。"""
import argparse
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import time
from run_draper import save

SUBDIRS=['MultiSource/Applications','MultiSource/Benchmarks/MiBench','MultiSource/Benchmarks/Ptrdist']


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--subdirectories',nargs='+',default=SUBDIRS)
    a=p.parse_args();source=a.source.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    os.nice(10);logs=out/'logs';logs.mkdir();commands=[]
    shutil.copyfile(__file__,out/'build_large_corpus.py')
    summary={'status':'incomplete','seed':42,'source':str(source),'release':'LLVM test-suite 23.1.5',
             'source_url':'https://github.com/llvm/llvm-test-suite','subdirectories':a.subdirectories,
             'programs':[],'failures':[],'counter_kind':'LLVM frontend instrumentation; sparse counters, not every block'}
    save(out/'summary.json',summary)
    def run(argv,allow_failure=False,timeout=1800,cwd=None):
        index=len(commands);start=time.monotonic()
        with (logs/f'{index:04d}.log').open('w') as log:
            r=subprocess.run(list(map(str,argv)),cwd=cwd,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
        commands.append({'argv':list(map(str,argv)),'cwd':str(cwd) if cwd else None,'returncode':r.returncode,'seconds':time.monotonic()-start,'log':f'logs/{index:04d}.log'})
        save(out/'commands.json',commands)
        if r.returncode and not allow_failure:raise RuntimeError(f'Failed command; see {logs / f"{index:04d}.log"}')
        return r.returncode
    try:
        for mode in ('native','instrumented'):
            build=out/mode
            flags='-Wno-error=implicit-function-declaration -gline-tables-only -save-temps=obj'
            command=['cmake','-S',source,'-B',build,'-G','Ninja','-DCMAKE_C_COMPILER=clang','-DCMAKE_CXX_COMPILER=clang++',
                     '-DCMAKE_BUILD_TYPE=Release','-DCMAKE_EXPORT_COMPILE_COMMANDS=ON',f'-DCMAKE_C_FLAGS={flags}',
                     '-DCMAKE_CXX_FLAGS=-gline-tables-only -save-temps=obj','-DTEST_SUITE_BENCHMARKING_ONLY=ON',
                     '-DTEST_SUITE_COPY_DATA=ON','-DTEST_SUITE_SUBDIRS='+';'.join(a.subdirectories)]
            if mode=='instrumented':command+=['-DTEST_SUITE_PROFILE_GENERATE=ON',f'-DTEST_SUITE_LLVM_PROFDATA={shutil.which("llvm-profdata")}']
            run(command)
            # 直接选子目录时 CMake 不复制父级测试配置，显式保留上游语义。
            for relative in ('MultiSource/lit.local.cfg','MultiSource/Benchmarks/lit.local.cfg','SingleSource/lit.local.cfg'):
                if (source/relative).exists():
                    (build/relative).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/relative,build/relative)
            tests=sorted(build.rglob('*.test'))
            if mode=='native':
                families={str(t.relative_to(build).parent) for t in tests}
                families=sorted(families);random.Random(42).shuffle(families)
                hold=max(1,len(families)//5)
                splits={f:'test' if i<hold else 'validation' if i<2*hold else 'development' for i,f in enumerate(families)}
                # 同一应用的编码/解码或多工作负载强制放在同一集合。
                def family(t):
                    parts=t.relative_to(build).parts
                    if parts[0]=='SingleSource':return '/'.join(parts[:-1])
                    return '/'.join(parts[:3] if parts[1]=='Applications' else parts[:4])
                groups=sorted({family(t) for t in tests});random.Random(42).shuffle(groups);hold=max(1,len(groups)//5)
                split_groups={f:'test' if i<hold else 'validation' if i<2*hold else 'development' for i,f in enumerate(groups)}
                summary['programs']=[{'test':str(t.relative_to(build)),'family':family(t),'split':split_groups[family(t)]} for t in tests]
                save(out/'configuration.json',{'seed':42,'programs':summary['programs'],'rule':'whole application family held together; fixed before executions'})
            print(f'{mode}: building {len(tests)} executable workloads sequentially',flush=True)
            run(['ninja','-C',build,'-j1','-k0'],allow_failure=True,timeout=7200)
            repeats=2 if mode=='instrumented' else 1
            for repeat in range(repeats):
                run(['lit','-v','-j1','--timeout=180','-o',build/f'results-{repeat}.json',build],allow_failure=True,timeout=7200)
                results=json.loads((build/f'results-{repeat}.json').read_text())
                statuses={r['name'].split(' :: ',1)[-1]:r for r in results['tests']}
                for record in summary['programs']:
                    r=statuses.get(record['test'],{'code':'MISSING'})
                    record[f'{mode}_{repeat}']={'status':r['code'],'metrics':r.get('metrics',{})}
                    if mode=='instrumented':
                        test=build/record['test'];binary=test.with_suffix('');profile=Path(str(binary)+'.profdata')
                        if profile.exists():
                            destination=out/'profiles'/record['test'].removesuffix('.test')/str(repeat)
                            destination.mkdir(parents=True,exist_ok=True)
                            shutil.copyfile(profile,destination/'counts.profdata')
                            text=subprocess.check_output(['llvm-profdata','show','--all-functions','--counts','--text',str(profile)],text=True)
                            (destination/'counts.txt').write_text(text)
                            record[f'profile_{repeat}']=str(destination.relative_to(out))
                save(out/'summary.json',summary)
            print(f'{mode}: executions and upstream checks finished',flush=True)
        compilation=json.loads((out/'instrumented/compile_commands.json').read_text())
        for record in summary['programs']:
            test=out/'instrumented'/record['test'];stem=test.stem
            units=[c for c in compilation if f'CMakeFiles/{stem}.dir/' in c['output'] and str(test.parent) in c['output']]
            record['source_files']=sorted({c['file'] for c in units})
            record['source_lines']=sum(len(Path(s).read_bytes().splitlines()) for s in record['source_files'])
            record['bitcode_files']=[str(b.relative_to(out)) for b in (test.parent/'CMakeFiles'/f'{stem}.dir').rglob('*.bc')]
            if all(f'profile_{i}' in record for i in (0,1)):
                text=[(out/record[f'profile_{i}']/'counts.txt').read_text() for i in (0,1)]
                record['repeated_counts_equal']=text[0]==text[1]
                record['profile_functions']=sum(1 for line in text[0].splitlines() if line.startswith('# Func Hash:'))
            record['usable']=all(record.get(k,{}).get('status')=='PASS' for k in ('native_0','instrumented_0','instrumented_1')) and 'profile_1' in record
        summary['executable_workloads']=len(summary['programs'])
        summary['application_families']=len({r['family'] for r in summary['programs']})
        summary['usable_workloads']=sum(r['usable'] for r in summary['programs'])
        summary['failures']=[r['test'] for r in summary['programs'] if not r['usable']]
        summary['status']='complete' if not summary['failures'] else 'partial'
        summary['input_scope']='upstream default inputs; repeated runs are not different inputs'
    finally:save(out/'summary.json',summary)


if __name__=='__main__':main()
