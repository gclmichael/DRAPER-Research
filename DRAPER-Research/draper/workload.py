"""执行上游命令及原有输出检查，保留标准输入、容差和非零退出码。"""
from pathlib import Path
import shlex
import subprocess
import os
import tempfile
import threading
import time


def description(test, directory, tools):
    lines=Path(test).read_text().splitlines()
    prefix='RUN: cd %S ; '
    if not lines[0].startswith(prefix):raise ValueError('Unsupported upstream command')
    tokens=shlex.split(lines[0][len(prefix):].replace('%S',str(directory)))
    stdin=None
    if '<' in tokens:
        i=tokens.index('<')
        if i!=len(tokens)-2:raise ValueError('Unsupported input redirection')
        stdin=str(Path(directory)/tokens[-1]);tokens=tokens[:i]
    if any(t in (';','|','>','&&','||') for t in tokens):raise ValueError('Unsupported shell operator')
    checks=[]
    for line in lines[1:]:
        if line.startswith('VERIFY: '):
            checks.append(shlex.split(line[8:].replace('%S',str(directory)).replace('%b',str(tools))))
    if not checks:raise ValueError('Missing upstream verification')
    return {'arguments':tokens[1:],'cwd':str(directory),'stdin':stdin,'checks':checks,'append_exit':True,
            'compiler':'clang++','link_flags':['-lm']}


def execute(binary, spec, env=None, timeout=300):
    stream=open(spec['stdin'],'rb') if spec.get('stdin') else open(os.devnull,'rb')
    try:
        # 上游用可定位的文件接收输出，音频编码器会回写文件头。
        with tempfile.TemporaryFile() as output:
            command=[str(binary),*spec.get('arguments',[])]
            started=time.perf_counter()
            process=subprocess.Popen(command,cwd=spec.get('cwd'),stdin=stream,
                stdout=output,stderr=subprocess.STDOUT,env={**os.environ,**(env or {})})
            expired=threading.Event()
            def stop():
                if process.poll() is None:
                    expired.set();process.kill()
            timer=threading.Timer(timeout,stop);timer.daemon=True;timer.start()
            try:
                # 带 timeout 的 wait 会按约 50 ms 轮询，不能拿它的返回时刻测短程序。
                returncode=process.wait()
                elapsed=time.perf_counter()-started
            finally:timer.cancel()
            if expired.is_set():raise subprocess.TimeoutExpired(command,timeout)
            output.seek(0);result=subprocess.CompletedProcess(command,returncode,output.read())
            result.elapsed_seconds=elapsed
            return result
    finally:
        if stream:stream.close()


def verify(result,spec,output):
    if 'expected_exit' in spec and result.returncode!=spec['expected_exit']:
        raise RuntimeError(f'Unexpected exit {result.returncode}: {result.stdout[-1000:]!r}')
    output=Path(output)
    output.write_bytes(result.stdout+(f'exit {result.returncode}\n'.encode() if spec.get('append_exit') else b''))
    checks=spec.get('checks',[[spec.get('verifier',''),'%o',spec.get('reference','')]])
    for command in checks:
        subprocess.run([str(output) if a=='%o' else a for a in command],check=True,cwd=spec.get('cwd'),capture_output=True,timeout=90)
