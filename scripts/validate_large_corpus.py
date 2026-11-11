"""检查实际语料的程序划分、计数对应、采样可复现性与上游测试结果。"""
import argparse
import json
from pathlib import Path
import numpy as np
from index_large_corpus import parse_counts
from run_draper import save


def main():
    p=argparse.ArgumentParser();p.add_argument('corpus',type=Path);p.add_argument('--index',default='paired-records-v2')
    a=p.parse_args();root=a.corpus.resolve();index=root/a.index
    summary=json.loads((index/'summary.json').read_text());failures=[];families={};nonrepeat=[]
    for program in summary['programs']:
        name=program['test'];directory=index/name.removesuffix('.test')
        try:
            if any(program[k]['status']!='PASS' for k in ('native_0','instrumented_0','instrumented_1')):
                raise ValueError('Upstream correctness test failed')
            family=program['family'];split=program['split']
            if family in families and families[family]!=split:raise ValueError('Application family crosses splits')
            families[family]=split
            exact=json.loads((directory/'exact-counters.json').read_text())['functions']
            if exact!=parse_counts((root/program['profile_0']/'counts.txt').read_text()):raise ValueError('Reference counter mismatch')
            repeated=parse_counts((root/program['profile_1']/'counts.txt').read_text())
            if exact!=repeated:nonrepeat.append(name)
            keys={(f['name'],f['profile_identity']):f for f in exact}
            mapped=set()
            for row in json.loads((directory/'counter-locations.json').read_text())['locations']:
                f=keys[(row['profile_name'],row['profile_identity'])]
                if row['counter_count']!=len(f['counts']) or not 0<=row['counter']<len(f['counts']):raise ValueError('Invalid counter position')
                if not (root/row['ir']).is_file():raise ValueError('Missing intermediate representation')
                mapped.add((row['profile_name'],row['profile_identity'],row['counter']))
            for f in exact:
                for i,value in enumerate(f['counts']):
                    if value and (f['name'],f['profile_identity'],i) not in mapped:raise ValueError('Nonzero counter has no position')
            for divisor in (16,64,256):
                observed=json.loads((directory/f'observations-1-{divisor}.json').read_text())
                if len(observed['functions'])!=len(exact):raise ValueError('Function list differs')
                random=np.random.default_rng(42)
                for f,o in zip(exact,observed['functions']):
                    expected=random.poisson(np.array(f['counts'],dtype=float)/divisor)
                    present=random.random(len(expected))>=.15;expected[~present]=0
                    if o!={'name':f['name'],'profile_identity':f['profile_identity'],'samples':expected.tolist(),'present':present.tolist()}:
                        raise ValueError('Observation cannot be reproduced')
        except (ValueError,KeyError,OSError) as error:failures.append({'program':name,'error':str(error)})
    report={'passed':not failures,'workloads':len(summary['programs']),'families':len(families),
            'paired_records':summary['records'],'failures':failures,'count_variation_between_runs':nonrepeat,
            'source_lines_by_distinct_path':sum(len(Path(f).read_bytes().splitlines()) for f in {f for p in summary['programs'] for f in p['source_files']}),
            'note':'Counter variation is retained; each reference describes its own execution. Synthetic sampling is checked independently.'}
    save(index/'validation.json',report)
    print(json.dumps(report,indent=2))
    if failures:raise SystemExit(1)


if __name__=='__main__':main()
