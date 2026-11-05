"""Build an offline video/label review page from a saved Ark test response.

This does not contact the model, start a server, or promote labels to training.
Unvalidated model results are preserved and explicitly identified as drafts.
"""
import argparse
import html
import json
from pathlib import Path


PHASE_NAMES = {
    'GRASP_CLUSTER': '抓取果串', 'PRESENT_CLUSTER': '举起并展示果串',
    'HOLD_STABLE': '保持果串稳定', 'SELECT_TARGET': '选择目标',
    'APPROACH_TARGET': '接近目标', 'GRASP_ATTEMPT': '尝试抓取',
    'RETRY_TARGET': '重试同一目标', 'DETACH_TARGET': '摘离番茄',
    'PLACE_IN_TRAY': '放入左侧果盘', 'ASSESS_REMAINING': '检查剩余',
    'RETURN_CLUSTER': '放回果串', 'REGRASP_AND_REORIENT': '换抓及转向',
    'RECOVERY': '恢复动作', 'IDLE': '等待', 'END': '结束',
}


def read(path):
    return json.loads(path.read_text())


def build(run, video, output, second_video=None):
    job = read(run / 'job.json')
    response = read(run / 'request/response.json')
    raw = response['text'].strip()
    if raw.startswith('```') and raw.endswith('```'):
        raw = '\n'.join(raw.splitlines()[1:-1])
    value = json.loads(raw)
    rows = value['steps']
    error = read(run / 'error.json') if (run / 'error.json').exists() else None
    passed = (run / 'labels.json').exists() and error is None
    status = '自动格式校验通过，语义待人工审查' if passed else '自动校验未通过，以下为模型原始草稿'
    notes = read(run / 'review_notes.json') if (run / 'review_notes.json').exists() else []
    placed = sorted({s['target_id'] for s in rows if s.get('phase') == 'PLACE_IN_TRAY'
                     and s.get('outcome') == 'success' and s.get('target_id')})
    payload = {'steps': rows, 'phases': PHASE_NAMES, 'notes': notes,
               'status': status, 'error': error, 'model': job['settings']['model'],
               'duration': job['length']/job['fps'], 'usage': response.get('usage', {}),
               'placed_ids': placed}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'review-data.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
    # The video is a copied local test input, referenced relatively for portability.
    import os
    video_url = os.path.relpath(video, output)
    second_video_url = os.path.relpath(second_video, output) if second_video else None
    data = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>番茄采摘标注审查 · Episode 0</title>
<style>
body{font:15px/1.6 system-ui,sans-serif;margin:0;background:#f5f6f8;color:#202832}
header{padding:22px 28px;background:#fff;border-bottom:1px solid #ddd}h1{font-size:24px;margin:0}
main{display:grid;grid-template-columns:minmax(400px,1fr) minmax(420px,1fr);gap:20px;padding:20px}
.viewer{position:sticky;top:15px;align-self:start}video{width:100%;background:#111;border-radius:8px}
article,.card{background:white;border:1px solid #dde1e6;border-radius:8px;padding:14px;margin-bottom:10px}
article.active{border:2px solid #3574cc;background:#eef5ff}article.uncertain{border-left:5px solid #bf7715}
button{cursor:pointer;border:0;border-radius:5px;background:#e7eef9;color:#204d86;padding:6px 10px}
small{color:#536173}.badge{padding:3px 7px;background:#fff0ca;border-radius:4px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}#notes{color:#8a4911}
@media(max-width:900px){main{display:block}.viewer{position:static}}
</style>
<header><h1>番茄串双臂采摘 · 第一条视频标注审查</h1><div id="summary"></div></header>
<main><section class="viewer"><strong>cam0</strong><video id="video" controls preload="metadata" src="VIDEO_URL"></video>
SECOND_VIDEO
<div class="card"><strong id="current">点击右侧时间跳转</strong><p>原始模型动作描述保持不变。下方是待核查事项，不代表已经人工确认。</p><div id="notes"></div></div>
<div class="card"><label>播放速度 <select id="speed"><option>0.5</option><option selected>1</option><option>1.5</option><option>2</option></select></label>
<p><label><input id="decisionsOnly" type="checkbox">仅显示含决策的段</label></p></div></section>
<section id="steps"></section></main>
<script>
const data=REVIEW_DATA;const video=document.getElementById('video');
document.getElementById('summary').textContent=`${data.status} · ${data.duration.toFixed(2)} 秒 · ${data.steps.length} 段 · ${data.model} · 模型声称成功放置 ${data.placed_ids.length} 个目标（未独立核实）`;
const notes=document.getElementById('notes');
if(data.error){const p=document.createElement('p');p.textContent='校验错误：'+data.error.message;notes.append(p)}
for(const n of data.notes){const p=document.createElement('p');p.textContent=n;notes.append(p)}
const root=document.getElementById('steps');
data.steps.forEach((s,i)=>{
 const row=document.createElement('article');row.dataset.index=i;
 if(s.evidence_status!=='observed')row.classList.add('uncertain');
 const b=document.createElement('button');b.textContent=`${i+1}. ${s.start_sec.toFixed(2)}–${s.end_sec.toFixed(2)} 秒`;
 b.onclick=()=>{video.currentTime=s.start_sec;video.pause();const other=document.getElementById('secondVideo');if(other){other.currentTime=s.start_sec;other.pause()}};row.append(b);
 const title=document.createElement('strong');title.textContent=' '+(data.phases[s.phase]||s.phase)+' · '+(s.target_id||'果串/目标未确定');row.append(title);
 const p=document.createElement('p');p.textContent=s.text;row.append(p);
 const hands=document.createElement('small');hands.textContent=`左手：${s.left_action??'未标注'}；右手：${s.right_action??'未标注'}；证据：${s.evidence_status}`;row.append(hands);
 if(['GRASP_ATTEMPT','DETACH_TARGET','PLACE_IN_TRAY'].includes(s.phase)){
  const ev=document.createElement('p');ev.textContent=`尝试 ${s.attempt_id??'未编号'} · 结果 ${s.outcome??'未知'}`+(s.outcome_evidence?` · 依据 ${s.outcome_evidence}`:'');row.append(ev)
 }else if(s.phase==='END'&&s.outcome_evidence){const ev=document.createElement('p');ev.textContent=`结束姿态：${s.outcome_evidence}`;row.append(ev)}
 if(s.decision){const d=document.createElement('details');const label=document.createElement('summary');label.textContent='决策与记忆：'+s.decision.choice;
 const pre=document.createElement('pre');pre.textContent=JSON.stringify(s.decision,null,2);d.append(label,pre);row.append(d)}
 root.append(row);
});
video.ontimeupdate=()=>{let current=-1;data.steps.forEach((s,i)=>{if(s.start_sec<=video.currentTime&&video.currentTime<s.end_sec)current=i});
 root.querySelectorAll('article').forEach(r=>r.classList.toggle('active',Number(r.dataset.index)===current));
 document.getElementById('current').textContent=current>=0?`当前第 ${current+1} 段 · ${data.phases[data.steps[current].phase]||data.steps[current].phase}`:'时间段外';};
document.getElementById('speed').onchange=e=>video.playbackRate=Number(e.target.value);
document.getElementById('decisionsOnly').onchange=e=>root.querySelectorAll('article').forEach(r=>r.hidden=e.target.checked&&!data.steps[Number(r.dataset.index)].decision);
</script></html>'''.replace('VIDEO_URL', html.escape(video_url, quote=True)).replace(
        'SECOND_VIDEO', '<strong>cam1</strong><video id="secondVideo" controls preload="metadata" src="'+html.escape(second_video_url, quote=True)+'"></video>' if second_video_url else '').replace('REVIEW_DATA', data)
    (output / 'review.html').write_text(page)
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    md = ['# 第一条视频自动标注结果', '', status, '',
          f"模型：`{job['settings']['model']}`；视频 {job['length']/job['fps']:.2f} 秒；{len(rows)} 段。", '',
          f'[逐段视频审查页]({output / "review.html"})', '',
          f'[本地原视频]({video})', '',
          f'[原始模型回复]({run / "request/response.json"})', '']
    if error:
        md += ['校验错误：'+error['message'], '']
    md += ['- '+n for n in notes]
    md += ['', '| 段 | 时间（秒） | 阶段 | 目标 | 决策 | 原始动作描述 |',
           '|---|---|---|---|---|---|']
    for i,s in enumerate(rows):
        md.append('| '+' | '.join(map(cell,[i+1,f"{s['start_sec']:.2f}–{s['end_sec']:.2f}",
            PHASE_NAMES.get(s.get('phase'),s.get('phase')),s.get('target_id') or '—',
            (s.get('decision') or {}).get('choice','—'),s['text']]))+' |')
    (output / 'review.md').write_text('\n'.join(md)+'\n')
    print(output / 'review.html')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--video',type=Path,required=True)
    parser.add_argument('--second-video',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    build(args.run.resolve(),args.video.resolve(),args.output.resolve(),
          args.second_video.resolve() if args.second_video else None)
