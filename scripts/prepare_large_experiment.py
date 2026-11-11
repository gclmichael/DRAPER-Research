"""将标准语料的多源程序接入现有全块计数与流恢复实验。"""
import argparse
import json
import os
from pathlib import Path
import shlex
import re
import shutil
import subprocess
import time
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
from draper.model import FlowSystem, simulate, fit
from run_draper import save
from draper.workload import description,execute,verify


def main():
    p=argparse.ArgumentParser();p.add_argument('corpus',type=Path);p.add_argument('--bridge',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--program',default='spiff')
    p.add_argument('--evaluate-heldout',action='store_true')
    p.add_argument('--nonlocal-flow',action='store_true')
    p.add_argument('--application-config',type=Path)
    a=p.parse_args();root=a.corpus.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    os.nice(10);commands=[];summary={'status':'incomplete','programs':[]}
    def run(argv,expected_exit=0,**kwargs):
        start=time.monotonic();r=subprocess.run(list(map(str,argv)),capture_output=True,timeout=300,**kwargs)
        commands.append({'argv':list(map(str,argv)),'seconds':time.monotonic()-start,'returncode':r.returncode,'stderr':r.stderr.decode(errors='replace')})
        save(out/'commands.json',commands)
        if r.returncode!=expected_exit:raise RuntimeError(f'Expected exit {expected_exit}, got {r.returncode}: '+r.stderr.decode(errors='replace')[-3000:])
        return r.stdout
    if a.application_config:
        application=json.loads(a.application_config.read_text())
        record={'source_lines':application['source_lines']}
        directory=out/a.program;directory.mkdir()
        execution=application['execution'];files=[Path(f) for f in application['bitcode_files']]
        workload=Path(execution['cwd'])
    else:
        record,execution,files,directory,workload=prepare_suite_case(root,out,a.program,a.evaluate_heldout)
    try:
        prepare_flow(run,a,record,execution,files,directory,summary)
    finally:save(out/'summary.json',summary)


def prepare_suite_case(root,out,program,evaluate_heldout):
    pointer=root/'current-index.json'
    index=root/json.loads(pointer.read_text())['summary'] if pointer.exists() else root/'paired-summary.json'
    records=json.loads(index.read_text())['programs']
    matching=[r for r in records if Path(r['test']).stem==program and r['usable']]
    if len(matching)!=1:raise ValueError('Expected one usable workload')
    record=matching[0]
    if record['split']!='development' and not evaluate_heldout:raise ValueError('Use --evaluate-heldout for frozen evaluation')
    directory=out/program;directory.mkdir();test=root/'native'/record['test']
    workload=directory/test.parent.name
    shutil.copytree(test.parent,workload,ignore=shutil.ignore_patterns('CMakeFiles','*.o','*.bc','*.ll','*.time','*.out','*.prof*'))
    execution=description(test,workload,root/'native/tools')
    native=execute(test.with_suffix(''),execution)
    verify(native,execution,directory/'native.output')
    execution['expected_exit']=native.returncode
    linking=subprocess.check_output(['ninja','-C',str(root/'native'),'-t','commands',record['test'].removesuffix('.test')],text=True)
    link=next(line for line in linking.splitlines() if ' -o '+record['test'].removesuffix('.test') in line)
    tail=link.split(' -o '+record['test'].removesuffix('.test'),1)[1].split('&&',1)[0]
    execution['link_flags']=shlex.split(tail)+['-lm']
    files=[root/filename.replace('instrumented/','native/',1) for filename in record['bitcode_files']]
    return record,execution,files,directory,workload


def prepare_flow(run,a,record,execution,files,directory,summary):
        run(['llvm-link',*files,'-o',directory/'linked.bc'])
        run(['opt','-passes=function(mem2reg,simplifycfg)','-S',directory/'linked.bc','-o',directory/'early.ll'])
        canonical=directory/'canonical.ll';graph=directory/'graph.json';bridge=a.bridge.resolve()
        run([bridge,'prepare',directory/'early.ll',f'--output={canonical}',f'--graph={graph}',f'--instrumented={directory / "instrumented.ll"}',*(['--nonlocal-flow'] if a.nonlocal_flow else [])])
        run(['opt','-passes=instrprof',*(['-instrprof-atomic-counter-update-all'] if execution.get('atomic_counters') else []),
             directory/'instrumented.ll','-o',directory/'instrumented.bc'])
        run([execution['compiler'],'-O3',directory/'instrumented.bc','-fprofile-instr-generate',*execution['link_flags'],'-o',directory/'instrumented'])
        collection_seconds=[]
        for repeat in range(2):
            raw=directory/f'exact-{repeat}.profraw'
            result=execute(directory/'instrumented',execution,{'LLVM_PROFILE_FILE':str(raw)})
            collection_seconds.append(result.elapsed_seconds)
            verify(result,execution,directory/f'exact-{repeat}.output')
            profile=directory/f'exact-{repeat}.profdata'
            run(['llvm-profdata','merge',raw,'-o',profile])
            run([bridge,'truth',canonical,f'--graph={graph}',f'--profile={profile}',f'--output={directory / f"exact-{repeat}.json"}'])
        system=FlowSystem(json.loads(graph.read_text()));truth=json.loads((directory/'exact-0.json').read_text())
        reference=system.vector(truth);audit=system.audit(reference)
        if max(audit['cfg_max_absolute_residual'],audit['call_max_absolute_residual'])>1e-6:
            raise ValueError(f'Measured flow is inconsistent: {audit}')
        observations=simulate(system,truth,1/64,.15,42);save(directory/'observations.json',observations)
        run([bridge,'profi',canonical,f'--graph={graph}',f'--profile={directory / "observations.json"}',f'--output={directory / "profi.json"}'])
        initial=system.vector(json.loads((directory/'profi.json').read_text()))
        estimate,solver=fit(system,observations,initial)
        for variant in ('restored','calibrated'):save(directory/f'{variant}.json',system.profile(estimate,'flow fit before sparse calibration'))
        save(directory/'anchors.json',{'anchors':[]})
        for variant in ('profi','restored','calibrated'):
            run([bridge,'annotate',canonical,f'--graph={graph}',f'--profile={directory / (variant+".json")}',
                 f'--output={directory / (variant+".ll")}',f'--decisions={directory / (variant+".decisions.json")}'])
        summary['programs'].append({'program':a.program,'execution':execution,'source_lines':record['source_lines'],
             'blocks':system.block_count,'edges':system.edge_count,'functions':len(system.functions),'exact_audit':audit,'solver':solver,
             'counts_repeat_equal':truth==json.loads((directory/'exact-1.json').read_text()),'collection_seconds':collection_seconds})
        summary['status']='complete'


if __name__=='__main__':main()
