"""Offline FIX-15 summary and paired analysis."""
from __future__ import annotations
import json, math, random, statistics
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
RUNS=ROOT/'runs/fix15'
OUT=ROOT/'docs/analysis/fix15'
GROUPS=('U-new','N','E-new')
VARIANTS={'FIX15-U-NEW':'U-new','FIX15-N':'N','FIX15-E-NEW':'E-new'}

def load():
    rows=[]
    for path in RUNS.rglob('summary.json'):
        d=json.loads(path.read_text(encoding='utf-8'))
        rows.append({
            'path':str(path.relative_to(ROOT)), 'task':d['task_id'],
            'group':VARIANTS[d['variant']], 'rep':int(path.parent.name[1:]),
            'solved':bool(d['solved']), 'status':d['status'],
            'edited':bool(d.get('changed_files')), 'first_edit':d.get('first_edit_step') is not None,
            'model_calls':int(d['model_calls']), 'tool_calls':int(d['tool_calls']),
            'cost':float(d['cost_cny']), 'stop':d.get('stop_reason'),
            'infra':d.get('infra_reason'), 'snapshot':d.get('snapshot_error'),
        })
    return sorted(rows,key=lambda x:(x['task'],x['group'],x['rep']))

def exact_mcnemar(a,b):
    aa=sum(x and not y for x,y in zip(a,b)); bb=sum((not x) and y for x,y in zip(a,b)); n=aa+bb
    if not n: return aa,bb,1.0
    tail=sum(math.comb(n,k) for k in range(min(aa,bb)+1))/2**n
    return aa,bb,min(1.0,2*tail)

def bootstrap(rows,a,b,iterations=20000,seed=15):
    tasks=sorted({r['task'] for r in rows})
    by={(r['task'],r['rep'],r['group']):r for r in rows}
    rng=random.Random(seed); diffs=[]
    for _ in range(iterations):
        sample=[tasks[rng.randrange(len(tasks))] for _ in tasks]
        va=sum(by[(t,rep,a)]['solved'] for t in sample for rep in (1,2))/(2*len(sample))
        vb=sum(by[(t,rep,b)]['solved'] for t in sample for rep in (1,2))/(2*len(sample))
        diffs.append(va-vb)
    diffs.sort(); lo=diffs[int(.025*iterations)]; hi=diffs[int(.975*iterations)-1]
    return lo,statistics.mean(diffs),hi

def main():
    rows=load(); by={(r['task'],r['rep'],r['group']):r for r in rows}
    assert len(rows)==180 and all(sum(r['group']==g for r in rows)==60 for g in GROUPS)
    lines=['# FIX-15 正式结果离线统计','',
           '这份统计读取 `runs/fix15` 中的 180 个 summary，不调用模型，也不修改正式结果。主要结论使用每个任务的两次重复作为配对单位。','']
    lines += ['## 运行完整性','',f'- 有效 summary 共 {len(rows)} 个，三组各 60 个。',
              '- 批次报告为 `halt_reason=null`，未结算预留为 0，总费用 30.476552 元。',
              '- N 组有一次 `delta_too_large` 基础设施失败。原始 r1 保留，重试结果已纳入该任务的最终观察。', '']
    lines += ['## 组间结果','', '| 组 | 解出 | 解出率 | 部分或完全通过 | 编辑率 | 首次编辑率 | 平均模型调用 | 平均费用 |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for g in GROUPS:
        rs=[r for r in rows if r['group']==g]; n=len(rs); solved=sum(r['solved'] for r in rs)
        pp=sum(r['status'] in ('pass','partial') for r in rs)
        ed=sum(r['edited'] for r in rs); fe=sum(r['first_edit'] for r in rs)
        lines.append(f'| {g} | {solved}/{n} | {solved/n:.1%} | {pp}/{n} ({pp/n:.1%}) | {ed/n:.1%} | {fe/n:.1%} | {statistics.mean(r["model_calls"] for r in rs):.1f} | {statistics.mean(r["cost"] for r in rs):.3f} 元 |')
    lines += ['', '解出率从 U-new 的 28.3% 提高到 N 的 38.3%，增加 10.0 个百分点。E-new 为 40.0%，比 N 高 1.7 个百分点。编辑率从 56.7% 提高到 N 的 83.3%，E-new 为 95.0%。平均模型调用从 63.5 降到 N 的 41.5，再降到 E-new 的 35.5。平均费用从 0.225 元降到 0.163 元，再降到 0.120 元。', '']
    lines += ['## 配对比较','', '| 比较 | 配对观察 | 新组独有解出 | 旧组独有解出 | 解出率差 | 精确 McNemar p | 任务级 bootstrap 95% 区间 |', '|---|---:|---:|---:|---:|---:|---:|']
    pvals=[]
    for a,b in [('N','U-new'),('E-new','N')]:
        av=[by[(t,rep,a)]['solved'] for t in sorted({r['task'] for r in rows}) for rep in (1,2)]
        bv=[by[(t,rep,b)]['solved'] for t in sorted({r['task'] for r in rows}) for rep in (1,2)]
        aa,bb,p=exact_mcnemar(av,bv); diff=(sum(av)/len(av)-sum(bv)/len(bv)); ci=bootstrap(rows,a,b); pvals.append(p)
        lines.append(f'| {a} 对 {b} | 60 | {aa} | {bb} | {diff:+.1%} | {p:.3f} | [{ci[0]:+.1%}, {ci[2]:+.1%}] |')
    # Holm adjustment for the two pre-specified comparisons
    order=sorted(range(2),key=lambda i:pvals[i]); adj=[0,0]
    for rank,i in enumerate(order): adj[i]=min(1,(2-rank)*pvals[i])
    lines += ['', f'两个预先指定比较的 Holm 校正 p 值为 N−U-new {adj[0]:.3f}，E-new−N {adj[1]:.3f}。这些 p 值只描述这批 60 个配对观察，不构成跨题库的普遍效应证明。', '']
    lines += ['## 任务级模式','', '按每道题两次运行的解出次数，N 比 U-new 更高的题有 6 道，更低的题有 3 道，21 道相同。E-new 比 N 更高的题有 3 道，更低的题有 2 道，25 道相同。N 的提升主要来自 holoviz、keras、pytest-dev、researchobject 等题。E-new 相对 N 的净变化很小，主要是 mpmath 和 packit 的改善抵消了 graphistry 与 tox 的下降。', '']
    lines += ['## 费用和行为','', '- 每解出一次的平均费用约为 U-new 0.795 元、N 0.425 元、E-new 0.300 元。', '- N 与 U-new 的配对平均模型调用差为 −22.1 次，E-new 与 N 的配对平均差为 −5.9 次。', '- N 与 U-new 的编辑率差为 +26.7 个百分点，E-new 与 N 的编辑率差为 +11.7 个百分点。', '- 这些费用和调用量是本批次的观察结果。解释上更像是干预减少了重复循环或更快进入编辑阶段，但当前数据没有把“减少调用”单独设计成因果因素。', '']
    lines += ['## 能回答的问题','', '- **“只加一句下一步提示是否改善找到后不编辑？”** 在本批次中，N 相对 U-new 的编辑率上升 26.7 个百分点，解出率上升 10.0 个百分点。配对方向与假设一致，但精确 McNemar p=0.180，区间仍包含 0，因此结果支持“可能有帮助”，还不足以把效应当成稳定定论。', '- **“完整结果解释是否比简单下一步提示更有效？”** E-new 相对 N 的解出率只上升 1.7 个百分点，编辑率上升 11.7 个百分点。解出结果没有显示明确的额外收益。', '- **“是否减少了重复循环和成本？”** E-new、N 的平均调用和费用低于 U-new，方向稳定。由于三组提示内容同时改变了模型的下一步行为，当前实验不能把成本差归因于某一个内部机制。', '']
    lines += ['## 证据边界','', '- 这是 30 道固定题、单一模型、每题两次重复的同批次比较。它回答的是该题集和该模型上的行为差异。', '- 运行中的一次账本恢复和一次候选快照重试没有改变任务配置，但应在正式报告中保留。', '- 结果不能证明 harness 限制在一般模型或其他题集上必然造成同样幅度的影响，也不能证明提示是唯一机制。', '']
    out=OUT/'FIX-15-INDEPENDENT-STATS.zh-CN.md'; out.parent.mkdir(parents=True,exist_ok=True); out.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(out)
if __name__=='__main__': main()
