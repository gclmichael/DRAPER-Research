"""基于 LLVM 伪探针（`@llvm.pseudoprobe`）的优化前后变换来源证据提取模块。

核心职责：
- `probes`：解析 LLVM IR 文本中的函数定义、基本块标签与 `@llvm.pseudoprobe(guid, probe_id, ...)` 内联调用。
- `transformation_evidence`：比对优化前早期 IR 与 `-O3` 优化后 IR 中的伪探针位置，
  识别唯一来源（unique origin）、跨函数内联（cross_function）、多副本克隆/展开（multiple_destinations）
  以及优化后丢失的探针（missing_probes），构建变换证据图（TEG）的拓扑对应关系。
"""
import re
from collections import defaultdict


def probes(text):
    function=block=None;result=[]
    for line in text.splitlines():
        match=re.match(r'define\b.*?@("[^"]+"|[^ (]+)\(',line)
        if match:function=match[1].strip('"');block='entry'
        match=re.match(r'([-a-zA-Z$._0-9]+):',line)
        if match:block=match[1]
        match=re.search(r'@llvm\.pseudoprobe\(i64 (-?\d+), i64 (\d+), i32 (\d+)',line)
        if match and function is not None and int(match[3])==0:
            result.append({'function':function,'block':block,'guid':int(match[1])%(2**64),'probe':int(match[2])})
        if line=='}':function=block=None
    return result


def transformation_evidence(early_text, optimized_text):
    origins=defaultdict(set);destinations=defaultdict(set)
    for p in probes(early_text):origins[(p['guid'],p['probe'])].add((p['function'],p['block']))
    for p in probes(optimized_text):destinations[(p['guid'],p['probe'])].add((p['function'],p['block']))
    rows=[]
    for key, targets in sorted(destinations.items()):
        sources=origins.get(key,set())
        rows.append({'guid':key[0],'probe':key[1],
                     'origins':[{'function':f,'block':b} for f,b in sorted(sources)],
                     'destinations':[{'function':f,'block':b} for f,b in sorted(targets)],
                     'origin_status':'unique' if len(sources)==1 else 'unknown',
                     'cross_function':any(f!=g for f,_ in sources for g,_ in targets),
                     'multiple_destinations':len(targets)>1,
                     'frequency_relation':'unknown until transform-specific count semantics verified'})
    return {'kind':'LLVM pseudo-probe provenance graph','relations':rows,
            'early_probes':len(origins),'optimized_probes':len(destinations),
            'missing_probes':len(origins.keys()-destinations.keys()),
            'unique_origin_relations':sum(r['origin_status']=='unique' for r in rows)}
