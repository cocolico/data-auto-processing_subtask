"""Build a local three-way acceptance page and a normal/speedup data bundle."""
import json
from pathlib import Path
import tarfile

from convest.config import WORKSPACE

ROOT = Path((WORKSPACE/'outputs/normal-speedup-acceptance/latest.txt').read_text().strip())
report = json.loads((ROOT/'acceptance.json').read_text())
if report['status'] != 'passed':
    raise ValueError('Acceptance data must pass before presenting it')
labels = json.loads((WORKSPACE/'scripts/annotation_experiment_assets/natural-language-labels.json').read_text())
translation = {phase: row['zh'] for phase, row in labels.items()}
versions = {}
for name, title in [('source','原始标注版'),('normal','normal'),('speedup','speedup')]:
    root = ROOT/name
    data = json.loads((root/'meta/annotation_granularity.jsonl').read_text())
    info = json.loads((root/'meta/info.json').read_text())
    video = root/info['video_path'].format(episode_index=0,episode_chunk=0,video_key='observation.images.cam0')
    versions[name] = {'title': title, 'data': data, 'video': str(video.relative_to(ROOT))}
mapping = json.loads((ROOT/'speedup/processing/episode_000000.json').read_text())
audit = mapping['annotation_alignment']
readme = f'''# 第四段视频：normal / speedup 双粒度标注验收

来源视频为远程 episode_000003；独立单条验收包内部重新编号为 episode_000000。
annotation_granularity.jsonl 中 source_episode_index=3 保留原编号。
原始标注来自已完成的第四段精简格式测试；本次没有再次调用模型、上传或训练。

| 版本 | 帧数 | 时长 | 细动作 | 粗阶段 |
|---|---:|---:|---:|---:|
| source | 2960 | 98.666667 s | 19 | 7 |
| normal | 2960 | 98.666667 s | 19 | 7 |
| speedup | {versions['speedup']['data']['length']} | {versions['speedup']['data']['length']/30:.6f} s | 19 | 7 |

全部输出保持 30 fps、三路 RGB、原生 Wuji 108/54 维。normal 视频字节不变；
speedup H264 重编码，使用与标签相同的最近源帧。所有帧标签、事件分段、映射、统计与视频帧数/PTS校验通过。

## 相比原转换新增的数据

1. normal/meta/annotation_granularity.jsonl：UTF-8 JSON Lines，每个 episode 一个对象。
   来源标注原样复制；细动作和粗阶段边界采用 normal 帧编号。
2. speedup/meta/annotation_granularity.jsonl：同一精简 schema v2。
   start_frame/end_frame 采用 speedup 帧编号，粗阶段由重新映射的细动作生成。
3. speedup/processing/episode_000000.json 中新增 annotation_alignment 对象：

| 字段 | JSON 类型 | 用途 |
|---|---|---|
| policy | string | 标注映射规则版本 |
| source_annotations_sha256 | string | normal 标注文件 SHA256 |
| source_frame_index | array<integer> | 每个输出观测/视频帧对应的源图像帧，长度等于 speedup 帧数 |
| fine_step_source_indices | array<integer> | 每个输出细动作对应的源细动作编号，从 0 开始 |
| skipped_fine_step_source_indices | array<integer> | 未被采到的源细动作编号，本次 {audit['skipped_fine_step_source_indices']} |

4. processing/normal.json、processing/summary.json 及完成凭据的 validation 中新增 annotations 对象：
   status 为 string；episodes/fine_steps/coarse_steps 为 integer；all_frame_labels_and_segments_checked 为 boolean。

没有新增 Parquet 列。observation.state 是 float32[108]，action 是 float32[54]，
timestamp 是 float32；frame_index/episode_index/index/task_index/source_timestamp_ns 是 int64。
source_index/action_source_index 已有，仍为 JSON number 数组（程序中 float64），不是本次新加。
现有 speed_profile、source_index 和 action_source_index 保持原算法输出。

## 标注文件中的类型

fine_steps/coarse_steps 为 array<object>；start_frame/end_frame 与 fine_step_indices 为整数/整数数组；
phase/text/text_zh 为 string；target_id 为 string 或 null；decision 为 object 或 null，
仅含 choice:string 和 target_id:string|null；return_condition 为 object 或 null，仅含
left_side_count:integer|null。excluded_intervals 每项仅含 fine_step_index:integer。
未恢复已删除的证据、结果、尝试编号等字段，也未新增重复的 annotation_events.jsonl。

## 原算法采样和验收方法

不做边界保护，不插入额外帧。重新独立调用原 AdaTempo 后逐项比较，
source_index 和 action_source_index 完全一致。标注按实际视频最近源帧映射。
未被采到的细动作不写入输出区间，其源编号记录在 skipped_fine_step_source_indices。
源标注保留完整；最后源时刻沿用原算法的下一时刻 action 语义。

打开网页选择同一动作，可将三版视频定位到各自对应的起点；三版按各自时间轴播放，不承诺按墙钟时间同步。
自动校验检查对应关系，不证明原模型识别必然正确。单条 AdaTempo 试验不等同于多 episode 批次运行，
多条输入的相位/节奏统计可能产生不同倍率，但标注对齐规则相同。

数据包 normal-speedup-fourth.tar.gz 包含 normal、speedup、来源记录、校验报告和完成清单。
source/ 是本机完整标注参考版，未装入派生数据包，以免重复携带深度等大字段。
'''
(ROOT/'README.md').write_text(readme)
with tarfile.open(ROOT/'normal-speedup-fourth.tar.gz','w:gz',compresslevel=1) as tar:
    for name in ('normal','speedup','source.json','normal.complete.json','speedup.complete.json','acceptance.json','README.md'):
        tar.add(ROOT/name,arcname=name)
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>第四段 · normal / speedup 对齐验收</title>
<style>body{margin:0;background:#0d1420;color:#e7eef7;font:15px/1.6 system-ui}header,main{padding:20px 28px}h1{font-size:25px;margin:0}h2{font-size:18px}p{color:#aebdd0}a{color:#90c9ff}button,select{background:#23344a;color:#edf5ff;border:1px solid #526983;border-radius:7px;padding:8px 12px;cursor:pointer;font:inherit}.players{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.card{background:#172232;padding:14px;border-radius:12px;position:relative}video{width:100%;background:#000}.caption{min-height:65px;color:#e5efff}.timer{color:#8fc9ff}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:11px;border-bottom:1px solid #344254}tr.selected{background:#244568}td button{text-align:left;width:100%;border:0;background:transparent}small{color:#9eafc5}.links{display:flex;gap:18px;flex-wrap:wrap}.scroll{overflow:auto}@media(max-width:950px){.players{grid-template-columns:1fr}video{max-height:45vh}}</style>
<header><h1>第四段视频 · 标注对齐验收</h1><p>19 个细动作 · 7 个粗阶段 · 三路相机数据已校验 · 页面展示 cam0 · 30 fps</p>
<p>原始 / normal：2960 帧，98.67 秒。speedup：SPEED_FRAMES 帧，SPEED_SECONDS 秒。已取消边界保护，与原算法采样时间轴完全一致。</p>
<div class="links"><a href="normal-speedup-fourth.tar.gz">下载 normal＋speedup 数据包</a><a href="README.md">新增字段与数据类型说明</a><a href="normal/meta/annotation_granularity.jsonl">normal 标注</a><a href="speedup/meta/annotation_granularity.jsonl">speedup 标注</a><a href="acceptance.json">完整校验报告</a></div></header>
<main><div class="players" id="players"></div><div class="toolbar"><button id="play">播放三版当前区间</button><button id="pause">全部暂停</button><label><input id="loop" type="checkbox">循环选中区间</label><select id="level"><option value="fine_steps">细粒度动作</option><option value="coarse_steps">粗粒度阶段</option></select></div><p>点击表格可定位三版的同一动作起点；各版按各自时间轴播放。细动作的时间范围使用左闭右开区间。</p><div class="scroll"><table><thead><tr><th>动作</th><th>原始标注版</th><th>normal</th><th>speedup</th></tr></thead><tbody id="rows"></tbody></table></div></main>
<script>
const versions=VERSIONS,zh=TRANSLATIONS;let level='fine_steps',selected=0;const videos={};
const fmt=x=>x.toFixed(2), range=(s,d)=>`${fmt(s.start_frame/d.fps)}–${fmt(s.end_frame/d.fps)} 秒 · 帧 [${s.start_frame}, ${s.end_frame})`;
for(const [key,v] of Object.entries(versions)){const card=document.createElement('section');card.className='card';const title=document.createElement('h2');title.textContent=v.title;const video=document.createElement('video');video.controls=true;video.preload='auto';fetch(v.video).then(r=>{if(!r.ok)throw new Error('视频读取失败');return r.blob();}).then(blob=>{const reader=new FileReader();reader.onload=()=>{video.src=reader.result;};reader.readAsDataURL(blob);}).catch(error=>{caption.textContent=error.message;});videos[key]=video;const timer=document.createElement('div');timer.className='timer';const caption=document.createElement('div');caption.className='caption';card.append(title,video,timer,caption);document.getElementById('players').append(card);
const update=()=>{const f=video.currentTime*v.data.fps;const step=v.data.fine_steps.find(s=>s.start_frame<=f&&f<s.end_frame);timer.textContent=`${fmt(video.currentTime)} / ${fmt(v.data.length/v.data.fps)} 秒`;caption.textContent=step?(zh[step.phase]||step.text):'播放结束';};video.addEventListener('timeupdate',()=>{const s=v.data[level][selected];if(document.getElementById('loop').checked&&video.currentTime>=s.end_frame/v.data.fps)video.currentTime=s.start_frame/v.data.fps;update();});video.addEventListener('loadedmetadata',update);video.addEventListener('seeked',update);}
function choose(i){selected=i;for(const [k,v] of Object.entries(versions)){videos[k].pause();videos[k].currentTime=v.data[level][i].start_frame/v.data.fps;}document.querySelectorAll('tr[data-index]').forEach(r=>r.classList.toggle('selected',Number(r.dataset.index)===i));}
function render(){const rows=document.getElementById('rows');rows.replaceChildren();versions.source.data[level].forEach((s,i)=>{const tr=document.createElement('tr');tr.dataset.index=i;const name=document.createElement('td');const b=document.createElement('button');b.textContent=`${i+1}. ${level==='fine_steps'?(zh[s.phase]||s.text):s.text_zh}`;b.onclick=()=>choose(i);name.append(b);tr.append(name);for(const v of Object.values(versions)){const td=document.createElement('td');td.textContent=range(v.data[level][i],v.data);tr.append(td);}rows.append(tr);});}
document.getElementById('level').onchange=e=>{level=e.target.value;render();choose(0);};document.getElementById('play').onclick=()=>{choose(selected);for(const v of Object.values(videos))v.play().catch(()=>{});};document.getElementById('pause').onclick=()=>{for(const v of Object.values(videos))v.pause();};render();
</script></html>'''
page=page.replace('SPEED_FRAMES', str(versions['speedup']['data']['length'])).replace('SPEED_SECONDS', f"{versions['speedup']['data']['length']/30:.2f}")
page=page.replace('VERSIONS',json.dumps(versions,ensure_ascii=False).replace('<','\\u003c')).replace('TRANSLATIONS',json.dumps(translation,ensure_ascii=False).replace('<','\\u003c'))
(ROOT/'index.html').write_text(page)
server='''from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit, unquote
import mimetypes,re
ROOT=Path(__file__).resolve().parent
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  path=unquote(urlsplit(self.path).path).lstrip('/') or 'index.html'
  p=(ROOT/path).resolve()
  if not p.is_relative_to(ROOT) or not p.is_file() or path.split('/')[0] not in {'index.html','README.md','acceptance.json','normal-speedup-fourth.tar.gz','normal','speedup','source'}:
   self.send_error(404);return
  size=p.stat().st_size;start=0;end=size-1;partial=False
  if self.headers.get('Range'):
   m=re.fullmatch(r'bytes=(\\d+)-(\\d*)',self.headers['Range'])
   if not m:self.send_error(416);return
   start=int(m[1]);end=min(int(m[2]) if m[2] else end,end);partial=True
   if start>end:self.send_error(416);return
  self.send_response(206 if partial else 200)
  self.send_header('Content-Type',mimetypes.guess_type(str(p))[0] or 'application/octet-stream')
  self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-store')
  if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
  self.end_headers()
  try:
   with p.open('rb') as stream:
    stream.seek(start);remaining=end-start+1
    while remaining:
     chunk=stream.read(min(1048576,remaining))
     if not chunk:break
     self.wfile.write(chunk);remaining-=len(chunk)
  except (BrokenPipeError,ConnectionResetError):pass
if __name__=='__main__':
 print('http://127.0.0.1:8885/',flush=True)
 ThreadingHTTPServer(('127.0.0.1',8885),Handler).serve_forever()
'''
(ROOT/'serve.py').write_text(server)
print(ROOT,flush=True)
