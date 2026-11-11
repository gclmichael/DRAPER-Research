"""为标准 LLVM 插桩计数建立配对观测与中间表示中的计数器位置索引。"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import numpy as np
from run_draper import save


def parse_counts(text):
    pattern=r'(?m)^([^\n#]+)\n# Func Hash:\n(\d+)\n# Num Counters:\n(\d+)\n# Counter Values:\n((?:\d+\n)+)'
    functions=[]
    for match in re.finditer(pattern,text):
        counts=list(map(int,match[4].split()))
        if len(counts)!=int(match[3]):raise ValueError('Counter length mismatch')
        functions.append({'name':match[1],'profile_identity':match[2],'counts':counts})
    if not functions:raise ValueError('No LLVM instrumentation counts found')
    return functions


def counter_locations(text):
    names={}
    for match in re.finditer(r'(?m)^(@(?:"[^"]+"|[-a-zA-Z$._0-9]+)) = .*?c"([^"\n]*)"',text):
        names[match[1]]=re.sub(r'\\([0-9A-Fa-f]{2})',lambda m:chr(int(m[1],16)),match[2]).rstrip('\0')
    function=block=None;rows=[]
    for line in text.splitlines():
        m=re.match(r'define\b.*?@("[^"]+"|[^ (]+)\(',line)
        if m:function=m[1].strip('"');block='entry'
        m=re.match(r'([-a-zA-Z$._0-9]+):',line)
        if m:block=m[1]
        m=re.search(r'@llvm\.instrprof\.increment(?:\.step)?\(ptr (@(?:"[^"]+"|[-a-zA-Z$._0-9]+)), i64 (-?\d+), i32 (\d+), i32 (\d+)(?:, i64 ([^)]+))?\)',line)
        if m:rows.append({'function':function,'block':block,'profile_name':names.get(m[1]),
                          'profile_identity':str(int(m[2])%(2**64)),'counter_count':int(m[3]),'counter':int(m[4]),
                          'increment':m[5] or '1','kind':'weighted-counter' if m[5] else 'unit-counter'})
        if line=='}':function=block=None
    return rows


def main():
    p=argparse.ArgumentParser();p.add_argument('corpus',type=Path);p.add_argument('--name',default='paired-records')
    a=p.parse_args();root=a.corpus.resolve()
    if Path(a.name).name!=a.name:raise ValueError('Index name must be a directory name')
    summary=json.loads((root/'summary.json').read_text());out=root/a.name;out.mkdir(exist_ok=False)
    shutil_source=out/'index_large_corpus.py';shutil_source.write_bytes(Path(__file__).read_bytes())
    rows=[]
    for program in summary['programs']:
        if not program['usable']:continue
        destination=out/program['test'].removesuffix('.test');destination.mkdir(parents=True)
        exact=parse_counts((root/program['profile_0']/'counts.txt').read_text());save(destination/'exact-counters.json',{'functions':exact})
        identities={(f['name'],f['profile_identity']):len(f['counts']) for f in exact}
        locations=[]
        for index,filename in enumerate(program['bitcode_files']):
            ir=destination/f'unit-{index:04d}.ll'
            subprocess.run(['llvm-dis',root/filename,'-o',ir],check=True,timeout=90)
            for location in counter_locations(ir.read_text()):
                location['ir']=str(ir.relative_to(root))
                location['matched']=identities.get((location['profile_name'],location['profile_identity']))==location['counter_count']
                locations.append(location)
        save(destination/'counter-locations.json',{'locations':locations,'matched':sum(r['matched'] for r in locations),
            'kind':'sparse LLVM instrumentation counters associated with saved frontend IR blocks'})
        mapped={(r['profile_name'],r['profile_identity'],r['counter']) for r in locations if r['matched']}
        program['unmapped_counter_slots']=sum((f['name'],f['profile_identity'],i) not in mapped for f in exact for i in range(len(f['counts'])))
        program['unmapped_nonzero_counters']=sum(value!=0 and (f['name'],f['profile_identity'],i) not in mapped
                                                for f in exact for i,value in enumerate(f['counts']))
        program['weighted_counter_sites']=sum(r['kind']=='weighted-counter' for r in locations)
        for divisor in (16,64,256):
            random=np.random.default_rng(42);functions=[]
            for f in exact:
                sampled=random.poisson(np.array(f['counts'],dtype=float)/divisor)
                present=random.random(len(sampled))>=.15;sampled[~present]=0
                functions.append({'name':f['name'],'profile_identity':f['profile_identity'],'samples':sampled.tolist(),'present':present.tolist()})
            path=destination/f'observations-1-{divisor}.json'
            save(path,{'kind':'synthetic Poisson observations of sparse instrumentation counters',
                       'rate':1/divisor,'missing_fraction':.15,'seed':42,'functions':functions})
            rows.append({'program':program['test'],'family':program['family'],'split':program['split'],
                         'observations':str(path.relative_to(root)),'exact':str((destination/'exact-counters.json').relative_to(root)),
                         'locations':str((destination/'counter-locations.json').relative_to(root))})
        program['profile_functions']=len(exact);program['sparse_counters']=sum(len(f['counts']) for f in exact)
        program['counter_locations']=len(locations);program['matched_counter_locations']=sum(r['matched'] for r in locations)
        print(program['test'],program['profile_functions'],'functions',program['matched_counter_locations'],'mapped counters',flush=True)
    (out/'samples.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    save(out/'summary.json',{'status':'complete','records':len(rows),'programs':summary['programs'],
        'source':'upstream default input per executable; frontend sparse counters',
        'sampling':'simulation; no new hardware samples'})
    save(root/'current-index.json',{'summary':str((out/'summary.json').relative_to(root)),
        'samples':str((out/'samples.jsonl').relative_to(root)),'path_base':'dataset root'})


if __name__=='__main__':main()
