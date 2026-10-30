import json,glob,os,sys,collections as C,re
SP=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,SP)
os.chdir("/Users/yangkaihao/zephyr-llm-repair")
import logging; logging.disable(logging.CRITICAL)
from lf_proto import Proto
from tools.log_filter import LogFilter
H=os.path.expanduser("~/zephyr-eval-work/")
DIRS=[H+"v3_pilot/logs_fixed",H+"v3_pilot/logs",H+"v3_migrate/logs",H+"v3_new/logs",H+"v3_fix/logs",H+"v3_expand/logs",H+"v3_qemucfg/logs",H+"v3_compfix/logs",H+"v3_real/logs"]
def raw(i):
    hits=[d+f"/{i}.mutated.log" for d in DIRS if os.path.exists(d+f"/{i}.mutated.log")]
    return hits
rows=[];miss=[];drift=[]
for name,fn in (("main","dataset/cases/final_dataset_v3.json"),("real","dataset/cases/real_bugs_v3.json")):
    for c in json.load(open(fn)):
        hs=raw(c['id'])
        if not hs: miss.append(c['id']); continue
        txt=open(hs[0],errors='replace').read()
        cur=LogFilter().compress_log(txt)
        if cur!=c['initial_error_log']:
            cand=[h for h in hs if LogFilter().compress_log(open(h,errors='replace').read())==c['initial_error_log']]
            if cand: txt=open(cand[0],errors='replace').read(); cur=c['initial_error_log']
            else: drift.append(c['id'])
        new,info=Proto().compress_log(txt)
        rows.append(dict(id=c['id'],set=name,cat=c['category'],changed=new!=cur,old=len(cur),new=len(new),**info))
print('total',len(rows),'missing raw',len(miss),miss[:10],'drift (raw+current filter != frozen)',len(drift),drift[:10])
ch=[r for r in rows if r['changed']]
print('changed',len(ch)); print('by set',C.Counter(r['set'] for r in ch)); print('by cat',C.Counter(r['cat'] for r in ch)); print('all by cat',C.Counter(r['cat'] for r in rows))
for k,f in (('FAILED unit added',lambda r:r['units']),('macro site added',lambda r:r['macros']),('runtime tail moved up',lambda r:r['moved'])):
    s=[r for r in rows if f(r)]; print(k,len(s),C.Counter(r['cat'] for r in s))
print('unchanged cats',C.Counter(r['cat'] for r in rows if not r['changed']))
print('size med old/new of changed',sorted(r['old'] for r in ch)[len(ch)//2],sorted(r['new'] for r in ch)[len(ch)//2],'max new',max(r['new'] for r in ch))
json.dump(rows,open(SP+'/lf_diff.json','w'),indent=1)
