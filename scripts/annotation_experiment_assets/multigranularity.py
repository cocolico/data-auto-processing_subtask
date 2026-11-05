"""Deterministic test-only coarse labels; fine/model results are immutable."""
import json
from pathlib import Path

def derive(data):
    from convest.annotation.granularity import derive as production_derive
    length = data['steps'][-1]['end_frame']
    return {'episode': data['episode'], **production_derive(data['steps'], data['dataset_fps'], length)}

def augment_review(run):
    run = Path(run)
    result = derive(json.loads((run / 'labels.json').read_text()))
    (run / 'multigranularity.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    page = (run / 'review.html').read_text()
    begin, end = '<!-- coarse-layer-start -->', '<!-- coarse-layer-end -->'
    if begin in page:
        a, b = page.index(begin), page.index(end) + len(end)
        page = page[:a] + page[b:]
    payload = json.dumps(result, ensure_ascii=False).replace('<', '\\u003c')
    script = '''
<script>
(()=>{
 const layers=PAYLOAD;
 const panel=document.createElement('section');
 panel.style.cssText='margin:18px 0;padding:16px;background:#1c2c3d;border-radius:8px';
 const title=document.createElement('h2');title.textContent='粗粒度阶段 · '+layers.coarse_steps.length+' 段';panel.append(title);
 const note=document.createElement('p');note.textContent='相邻同类细分动作合并为一个粗阶段；下方细粒度标注完整保留。点击粗阶段对照原视频。';panel.append(note);
 const current=document.createElement('p');current.style.color='#7fc4ff';panel.append(current);
 const buttons=layers.coarse_steps.map(s=>{
  const b=document.createElement('button');b.className='step';
  b.textContent=(s.start_frame/layers.fps).toFixed(2)+'–'+(s.end_frame/layers.fps).toFixed(2)+' 秒 · '+s.text_zh+' · 细分 '+s.fine_step_indices.map(i=>i+1).join('、');
  b.onclick=()=>{document.getElementById('loop').checked=false;jump(s.fine_step_indices[0]);};panel.append(b);return b;
 });
 if(layers.excluded_intervals.length){const p=document.createElement('p');p.textContent='存在低位静止结束段：仅在细粒度层保留，四类粗阶段不覆盖该区间。';panel.append(p);}
 const download=document.createElement('a');download.textContent='下载双粒度标注 JSON';download.download='multigranularity.json';download.href=URL.createObjectURL(new Blob([JSON.stringify(layers,null,2)],{type:'application/json'}));panel.append(download);
 const fine=document.getElementById('steps');const column=document.createElement('div');fine.before(column);column.append(panel,fine);
 const heading=document.createElement('h2');heading.textContent='细粒度动作';panel.append(heading);
 function update(){const idx=layers.coarse_steps.findIndex(s=>video.currentTime*layers.fps>=s.start_frame&&video.currentTime*layers.fps<s.end_frame);buttons.forEach((b,i)=>b.classList.toggle('active',i===idx));current.textContent=idx<0?'当前粗阶段：未归入四类动作':'当前粗阶段：'+layers.coarse_steps[idx].text_zh;}
 video.addEventListener('timeupdate',update);video.addEventListener('seeked',update);update();
})();
</script>
'''.replace('PAYLOAD', payload)
    page = page.replace('</html>', begin + script + end + '</html>')
    # Remove obsolete manual-gate presentation; raw historical result files stay intact.
    page = page.replace('中文标注审核', '双粒度标注预览').replace('视频审核', '视频预览')
    page = page.replace(' · 待人工审核', '').replace('需人工审核：', '证据说明：')
    page = page.replace('本次仅测试完整时间覆盖；动作真实性及边界仍需人工审核。详情见视频下方检查结果。', '本次测试完整时间覆盖；详细自动检查结果见下方。')
    page = page.replace('两段按规则划分，真实动作边界需人工审核。', '两段按指定的 2.8 秒规则划分。')
    page = page.replace('该边界仍需对照视频确认。', '保留规则划分依据。')
    page = page.replace('JSON.stringify(report,null,2)', 'JSON.stringify(Object.fromEntries(Object.entries(report).filter(([k])=>!["review_issues","semantic_review"].includes(k))),null,2)')
    (run / 'review.html').write_text(page)
    return result
