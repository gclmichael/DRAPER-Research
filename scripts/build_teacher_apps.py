"""构建现代 SQLite、zstd、FFmpeg，并产生可直接交给恢复器的工作负载说明。"""
import argparse,json,os,re,shutil,subprocess,tarfile,time,zipfile
from pathlib import Path
from run_draper import save
from draper.workload import execute,verify


def unpack(archive,destination):
    destination.mkdir()
    if archive.suffix=='.zip':
        with zipfile.ZipFile(archive) as z:
            if any(Path(n).is_absolute() or '..' in Path(n).parts for n in z.namelist()):raise ValueError('Invalid source archive path')
            z.extractall(destination)
            for item in z.infolist():
                permissions=(item.external_attr>>16)&0o777
                if permissions:(destination/item.filename).chmod(permissions)
    else:
        with tarfile.open(archive) as t:t.extractall(destination,filter='data')
    directories=[p for p in destination.iterdir() if p.is_dir()]
    if len(directories)!=1:raise ValueError('Expected one source root')
    return directories[0]


def compiled_sources(bitcode,root):
    result=set()
    for path in bitcode:
        process=subprocess.Popen(['llvm-dis',str(path),'-o','-'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        try:
            filename=None
            for _ in range(20):
                line=process.stdout.readline()
                match=re.match(r'source_filename = "([^"]+)"',line)
                if match:filename=Path(match[1]);break
            if filename is None:raise ValueError(f'Missing source filename: {path}')
        finally:process.stdout.close();process.wait()
        candidates=[p.resolve() for p in (root/filename,root/'programs'/filename) if p.is_file() and p.resolve().is_relative_to(root)]
        if not candidates:candidates=[p.resolve() for p in root.rglob(filename.name)]
        if len(set(candidates))!=1:raise ValueError(f'Ambiguous compiled source: {filename}')
        result.add(candidates[0])
    return sorted(result)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--resume',action='store_true',help='Preserve successful applications and archive failed build directories before retrying')
    a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=a.resume);os.nice(10)
    previous={}
    if a.resume and (out/'summary.json').exists():
        previous={row['program']:row for row in json.loads((out/'summary.json').read_text())['programs']}
        attempt=1+len(list(out.glob('summary-before-retry-*.json')))
        shutil.copyfile(out/'summary.json',out/f'summary-before-retry-{attempt}.json')
        shutil.copyfile(out/'build_teacher_apps.py',out/f'build_teacher_apps-before-retry-{attempt}.py')
    shutil.copyfile(__file__,out/'build_teacher_apps.py')
    base=Path('/data/chaoni/chengli');archives=base/'draper-industrial-2026-11-09'
    inputs=out/'inputs';inputs.mkdir(exist_ok=a.resume);fpcmp=base/'draper-node3/data/industrial-corpus-v1/native/tools/fpcmp-target'
    sql=inputs/'queries.sql'
    sql.write_text('''PRAGMA journal_mode=OFF;
CREATE TABLE items(id INTEGER PRIMARY KEY, grp INTEGER, value INTEGER, label TEXT);
WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<200000)
INSERT INTO items SELECT x, x%1000, (x*73)%100000, printf('item-%08d',x) FROM n;
CREATE INDEX group_index ON items(grp,value);
SELECT count(*),sum(value),min(label),max(label) FROM items;
SELECT grp,sum(value) FROM items GROUP BY grp ORDER BY sum(value),grp LIMIT 30;
SELECT a.grp,sum(a.value+b.value) FROM items a JOIN items b ON b.id=a.id+1 WHERE a.id%10=0 GROUP BY a.grp ORDER BY a.grp;
SELECT id,value FROM items ORDER BY value DESC,id LIMIT 100;
''')
    sql_test=inputs/'queries-test.sql';sql_test.write_text(sql.read_text().replace('200000','300000').replace('x%1000','x%1009'))
    source=base/'draper-node3/data/large-sources/test-suite-23.1.5.src'
    text=inputs/'source-code.txt'
    with text.open('wb') as f:
        for path in sorted((source/'MultiSource/Applications').rglob('*.c')):
            f.write(path.read_bytes());f.write(b'\n')
    text_test=inputs/'benchmark-source-code.txt'
    with text_test.open('wb') as f:
        for path in sorted((source/'MultiSource/Benchmarks/MiBench').rglob('*.c')):
            f.write(path.read_bytes());f.write(b'\n')
    configurations={
      'sqlite-modern':{'archive':archives/'sqlite-src-3540100.zip','arguments':[':memory:'],'stdin':str(sql)},
      'zstd':{'archive':archives/'zstd-1.5.7.tar.gz','arguments':['-q','--single-thread','-c','-3',str(text)]},
      'ffmpeg':{'archive':base/'draper-node3/data/teacher-suite-sources/ffmpeg-8.0.1.tar.xz',
        'arguments':['-hide_banner','-loglevel','error','-filter_threads','1','-filter_complex_threads','1','-f','lavfi','-i','testsrc2=size=1280x720:rate=30',
          '-frames:v','90','-vf','scale=640:360','-threads','1','-f','framemd5','-']}}
    results=[]
    for name,config in configurations.items():
      if previous.get(name,{}).get('status')=='complete':
        results.append(previous[name]);continue
      if (out/name).exists():
        if not a.resume:raise FileExistsError(out/name)
        archive=out/f'{name}.failed-{1+len(list(out.glob(name+".failed-*")))}'
        (out/name).rename(archive)
      directory=out/name;directory.mkdir();commands=[];record={'program':name,'status':'running','archive':str(config['archive']),
        'split':'development' if name=='sqlite-modern' else 'test'}
      def run(argv,cwd,timeout=1800):
        index=len(commands);start=time.monotonic();log=directory/f'command-{index:03d}.log'
        with log.open('wb') as f:r=subprocess.run(list(map(str,argv)),cwd=cwd,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
        commands.append({'argv':list(map(str,argv)),'cwd':str(cwd),'returncode':r.returncode,'seconds':time.monotonic()-start})
        save(directory/'commands.json',commands)
        if r.returncode:raise RuntimeError(f'Build failed: {log}')
      try:
        for mode in ('native','instrumented'):
          src=unpack(config['archive'],directory/mode)
          flags='-O3 -gline-tables-only -save-temps=obj'+(' -fprofile-instr-generate -fprofile-update=atomic' if mode=='instrumented' else '')
          if name=='sqlite-modern':
            run(['./configure','--disable-shared','--disable-readline','CC=clang','CFLAGS='+flags],src)
            run(['make','-j1','sqlite3'],src);binary=src/'sqlite3'
          elif name=='zstd':
            run(['make','-j1','zstd','CC=clang','CFLAGS='+flags+' -DZSTD_DISABLE_ASM','LDFLAGS=-pthread'+(' -fprofile-instr-generate' if mode=='instrumented' else ''),
                 'HAVE_ZLIB=0','HAVE_LZMA=0','HAVE_LZ4=0','ZSTD_LEGACY_SUPPORT=0'],src);binary=src/'programs/zstd'
          else:
            run(['./configure','--cc=clang','--extra-cflags='+flags,'--extra-ldflags='+(' -fprofile-instr-generate' if mode=='instrumented' else ''),
                '--disable-shared','--enable-static','--disable-autodetect','--disable-x86asm','--disable-asm','--disable-doc',
                '--disable-network','--disable-everything','--disable-ffprobe','--disable-ffplay','--enable-ffmpeg',
                '--enable-protocol=file,pipe','--enable-indev=lavfi','--enable-filter=testsrc2,scale,format,null',
                '--enable-muxer=framemd5','--enable-encoder=rawvideo','--enable-decoder=wrapped_avframe'],src)
            (src/'draper-keep-generated.mk').write_text('.PRECIOUS: %.css.c %.css.min %.css.min.gz %.html.gz %.html.c\n')
            run(['make','-f','Makefile','-f','draper-keep-generated.mk','-j1','ffmpeg'],src);binary=src/'ffmpeg'
          spec={'arguments':config['arguments'],'stdin':config.get('stdin'),'cwd':str(inputs),'expected_exit':0,'append_exit':False,
                'compiler':'clang++','link_flags':['-lm','-pthread','-ldl'],'atomic_counters':True,
                'checks':[[str(fpcmp),'%o',str(directory/'reference.output')]]}
          dependencies=subprocess.check_output(['ldd',str(binary)],text=True)
          for library in re.findall(r'^\s*lib([^\s/]+?)\.so',dependencies,re.M):
            flag='-l'+library
            if library not in ('c','gcc_s','stdc++') and flag not in spec['link_flags']:spec['link_flags'].append(flag)
          evaluation={**spec,'checks':[[str(fpcmp),'%o',str(directory/'reference-test.output')]]}
          if name=='sqlite-modern':evaluation['stdin']=str(sql_test)
          elif name=='zstd':evaluation['arguments']=['-q','--single-thread','-c','-5',str(text_test)]
          else:evaluation['arguments']=[v.replace('1280x720','1920x1080').replace('640:360','1280:720') if v!='90' else '120' for v in spec['arguments']]
          spec['evaluation']=evaluation
          repeats=1 if mode=='native' else 2
          for repeat in range(repeats):
            raw=directory/f'counts-{repeat}.profraw'
            result=execute(binary,spec,{'LLVM_PROFILE_FILE':str(raw)},timeout=300)
            if result.returncode:raise RuntimeError(result.stdout[-2000:].decode(errors='replace'))
            if mode=='native':
                (directory/'reference.output').write_bytes(result.stdout)
                record['native_train_seconds']=result.elapsed_seconds
                record['native_binary']=str(binary);record['native_source']=str(src)
                test_result=execute(binary,evaluation,timeout=300)
                if test_result.returncode:raise RuntimeError('Independent evaluation input failed')
                (directory/'reference-test.output').write_bytes(test_result.stdout)
                record['native_evaluation_seconds']=test_result.elapsed_seconds
            else:
                record.setdefault('profile_run_seconds',[]).append(result.elapsed_seconds)
                verify(result,spec,directory/f'instrumented-{repeat}.output')
                run(['llvm-profdata','merge',raw,'-o',directory/f'counts-{repeat}.profdata'],src)
          if mode=='native':
            bc=sorted(src.rglob('*.bc'))
            if name=='sqlite-modern':bc=[b for b in bc if b.stem in ('sqlite3','shell','sqlite3-shell','sqlite3-sqlite3')]
            if name=='zstd':bc=[b for b in bc if b.is_relative_to(src/'programs/obj')]
            if not bc:raise RuntimeError('No saved frontend bitcode')
            record['bitcode_files']=[str(b) for b in bc]
            sources=compiled_sources(bc,src)
            record['source_files']=[str(f) for f in sources]
            record['source_lines']=sum(len(f.read_bytes().splitlines()) for f in sources)
            record['execution']=spec
        record['status']='complete'
        save(directory/'application.json',record)
      except Exception as error:record['status']='failed';record['error']=repr(error)
      results.append(record);save(out/'summary.json',{'status':'finished' if len(results)==len(configurations) else 'running','programs':results})
      print(name,record['status'],record.get('error',''),flush=True)
    save(out/'summary.json',{'status':'finished','programs':results})


if __name__=='__main__':main()
