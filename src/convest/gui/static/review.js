"use strict";
window.reviewUI = (() => {
  const phases = {
    GRASP_CLUSTER:["抓稳整串","#397be6"], PRESENT_CLUSTER:["抬至中央","#31a1c0"],
    APPROACH_AND_GRASP:["接近抓果","#9a70dd"], DETACH_AND_PLACE:["摘下放置","#e2a43a"],
    RETURN_AND_RELEASE:["放回松手","#36a783"], REORIENT_STEM:["转梗调整","#dc7586"], END:["低位静止","#8492a7"],
    PICK_UP:["拿起番茄","#397be6"], PICK_TOMATO:["摘取番茄","#e2a43a"],
    PUT_BACK:["放回番茄","#36a783"], ROTATE_STEM:["旋转番茄杆","#dc7586"]
  };
  const names = {cam0:"外部总览",cam1:"右手腕",cam2:"左手腕"};
  const videoIds = {cam0:"annotation-video",cam1:"annotation-video-cam1",cam2:"annotation-video-cam2"};
  let data=null, task=null, active=[], master=null, wantPlay=false, raf=0, generation=0;
  let currentIndex=-1, loopIndex=-1, exportTimer=0, pollingExport=false;
  const phaseName = s => phases[s]?.[0] || s || "未分类";
  const color = s => phases[s]?.[1] || "#8492a7";
  const duration = () => data ? data.frames/data.fps : 0;
  const frameAt = t => Math.max(0,Math.min((data?.frames||1)-1,Math.floor(t*(data?.fps||1)+0.001)));
  const clock = t => `${String(Math.floor(t/60)).padStart(2,"0")}:${(t%60).toFixed(3).padStart(6,"0")}`;
  const currentFrame = () => frameAt(master?.currentTime || 0);
  const segmentAt = frame => data?.steps.findIndex(s=>s.start_frame<=frame&&frame<s.end_frame) ?? -1;
  function pause() {
    wantPlay=false; active.forEach(v=>v.pause());cancelAnimationFrame(raf);
    $("review-play").textContent="播放";
  }
  function clear() {
    pause();generation++;clearTimeout(exportTimer);data=null;task=null;active=[];master=null;currentIndex=-1;
    Object.entries(videoIds).forEach(([c,id])=>{const v=$(id);v.onloadedmetadata=v.oncanplay=v.onseeked=v.onwaiting=v.onerror=v.onended=null;v.removeAttribute("src");v.load();$("review-status-"+c).textContent="未载入";});
    ["annotation-steps","annotation-coarse-steps","review-summary","review-fine-track","review-coarse-track","review-legend","review-ruler"].forEach(id=>$(id).replaceChildren());
    $("review-inspector").innerHTML='<p class="hint">打开结果后显示当前动作、目标与时间边界。</p>';
    $("annotation-coarse-info").textContent="";$("review-time").textContent="00:00.000 / 00:00.000";$("review-frame").textContent="帧 —";$("review-sync").textContent="等待视频";
    $("review-scrubber").value=0;$("review-scrubber").disabled=true;$("review-play").disabled=true;
    ["rrd","mcap","json"].forEach(k=>$("review-export-"+k).disabled=true);
    $("review-export-download").hidden=true;$("review-export-status").textContent="打开结果后检查本机可用的可视化 SDK。";
  }
  function seek(frame, selectLoop=false) {
    if(!data)return;
    frame=Math.max(0,Math.min(data.frames-1,Math.round(frame)));
    if(selectLoop)loopIndex=segmentAt(frame);
    active.forEach(v=>{v.pause();if(v.readyState>=1)v.currentTime=frame/data.fps;});
    update(frame);if(wantPlay)reconcile();
  }
  function tick() {
    if(!data||!master)return;
    const frame=currentFrame(), loop=data.steps[loopIndex];
    if($("review-loop").checked&&loop&&master.currentTime>=loop.end_frame/data.fps){seek(loop.start_frame);}
    else {
      active.filter(v=>v!==master&&!v.seeking&&v.readyState>=2).forEach(v=>{
        if(Math.abs(v.currentTime-master.currentTime)>1.5/data.fps)v.currentTime=master.currentTime;
      });
      update(frame);
    }
    if(wantPlay)raf=requestAnimationFrame(tick);
  }
  async function reconcile() {
    if(!wantPlay||!active.length)return;
    if(active.some(v=>v.readyState<3||v.seeking)){
      active.forEach(v=>v.pause());$("review-sync").textContent="正在缓冲 / 对齐三路视频…";return;
    }
    const expected=generation;
    try {await Promise.all(active.map(v=>v.play()));}
    catch(e){if(expected===generation&&wantPlay){pause();$("review-sync").textContent="播放暂不可用，请再次点击播放。";}return;}
    if(expected!==generation||!wantPlay){active.forEach(v=>v.pause());return;}
    $("review-play").textContent="暂停";cancelAnimationFrame(raf);raf=requestAnimationFrame(tick);
  }
  function togglePlay() {
    if(!data||!master)return;
    if(wantPlay){pause();return;}
    if(master.ended||master.currentTime>=duration()-1/data.fps)seek(0);
    wantPlay=true;$("review-play").textContent="暂停";reconcile();
  }
  function focus(camera) {
    document.querySelectorAll("#review-videos figure").forEach(f=>f.classList.toggle("focused",f.dataset.camera===camera.replace("observation.images.","")));
  }
  function track(id, segments, coarse=false) {
    $(id).innerHTML=segments.map((s,i)=>`<button class="review-segment" style="left:${s.start_frame/data.frames*100}%;width:${(s.end_frame-s.start_frame)/data.frames*100}%;--phase-color:${color(s.phase)}" data-start="${s.start_frame}" title="${esc(phaseName(s.phase))} · ${(s.start_frame/data.fps).toFixed(2)}–${(s.end_frame/data.fps).toFixed(2)} 秒" aria-label="${coarse?"粗阶段":"细动作"} ${i+1}：${esc(phaseName(s.phase))}">${(s.end_frame-s.start_frame)/data.frames>.055?esc(coarse?phaseName(s.phase):i+1):""}</button>`).join("")+'<span class="review-playhead" aria-hidden="true"></span>';
    $(id).querySelectorAll("[data-start]").forEach(b=>b.onclick=()=>seek(Number(b.dataset.start),true));
  }
  function tables() {
    if(!data)return;
    const filter=$("review-filter").value;
    $("annotation-steps").innerHTML=data.steps.map((s,i)=>filter&&s.phase!==filter?"":`<tr data-step="${i}"><td><button data-start="${s.start_frame}" aria-label="跳到动作 ${i+1}">${String(i+1).padStart(2,"0")}</button></td><td>${(s.start_frame/data.fps).toFixed(2)}–${(s.end_frame/data.fps).toFixed(2)} s<br><small>[${s.start_frame}, ${s.end_frame}) 帧</small></td><td><span class="review-phase" style="--phase-color:${color(s.phase)}">${esc(phaseName(s.phase))}</span> ${s.target_id?`<span class="tag">${esc(s.target_id)}</span>`:""}<p>${esc(s.text)}</p></td></tr>`).join("");
    const coarse=data.granularity?.coarse_steps||[];
    $("annotation-coarse-steps").innerHTML=coarse.map((s,i)=>`<tr data-coarse="${i}"><td><button data-start="${s.start_frame}">${i+1}</button></td><td>${(s.start_frame/data.fps).toFixed(2)}–${(s.end_frame/data.fps).toFixed(2)}</td><td>${esc(s.text_zh)}<br><small>细步骤 ${s.fine_step_indices.map(j=>j+1).join("、")}</small></td></tr>`).join("");
    ["annotation-steps","annotation-coarse-steps"].forEach(id=>$(id).querySelectorAll("[data-start]").forEach(b=>b.onclick=()=>seek(Number(b.dataset.start),true)));
    currentIndex=-1;update(currentFrame());
  }
  function inspect(index) {
    const s=data.steps[index];if(!s)return;
    const decisions={CONTINUE_PICK:"继续采摘",RETRY_SAME_TARGET:"重试当前目标",REGRASP:"重新抓握",STOP:"停止",UNKNOWN:"未知"};
    const coarse=data.granularity?.coarse_steps.find(c=>c.start_frame<=s.start_frame&&s.start_frame<c.end_frame);
    $("review-inspector").innerHTML=`<span class="review-phase" style="--phase-color:${color(s.phase)}">${esc(phaseName(s.phase))}</span><h3>动作 ${index+1} <small>/ ${data.steps.length}</small></h3><p class="review-instruction">${esc(s.text)}</p><dl><dt>细分类别</dt><dd>${esc(s.phase||"旧版标注")}</dd><dt>粗阶段</dt><dd>${esc(coarse?.text_zh||"未映射 / 静止区间")}</dd><dt>起止时间</dt><dd>${(s.start_frame/data.fps).toFixed(3)}–${(s.end_frame/data.fps).toFixed(3)} s</dd><dt>帧范围</dt><dd>[${s.start_frame}, ${s.end_frame})</dd><dt>时长</dt><dd>${((s.end_frame-s.start_frame)/data.fps).toFixed(3)} s · ${s.end_frame-s.start_frame} 帧</dd><dt>目标番茄</dt><dd>${esc(s.target_id||"未指定 / 不确定")}</dd><dt>决策</dt><dd>${esc(s.decision?decisions[s.decision.choice]||s.decision.choice:"未标注")}${s.decision?.target_id?" · "+esc(s.decision.target_id):""}</dd><dt>放回时左侧番茄数</dt><dd>${s.return_condition?esc(s.return_condition.left_side_count??"被遮挡 / 未确定"):"不适用"}</dd></dl><button id="review-segment-start">回到本段起点</button><p class="hint">左侧数量按 cam0 中右手抓点的画面左侧计算。帧编号从 0 开始，右边界不包含在片段内。</p>`;
    $("review-segment-start").onclick=()=>seek(s.start_frame,true);
  }
  function update(frame) {
    if(!data)return;
    const time=master?.currentTime||frame/data.fps;
    $("review-time").textContent=`${clock(time)} / ${clock(duration())}`;
    $("review-frame").textContent=`帧 ${frame} / ${data.frames-1}`;$("review-scrubber").value=frame;
    document.querySelectorAll(".review-playhead").forEach(p=>p.style.left=`${frame/data.frames*100}%`);
    const index=segmentAt(frame);
    if(index!==currentIndex){currentIndex=index;inspect(index);}
    $("annotation-steps").querySelectorAll("[data-step]").forEach(r=>r.classList.toggle("current-step",Number(r.dataset.step)===index));
    $("annotation-coarse-steps").querySelectorAll("[data-coarse]").forEach(r=>{const s=data.granularity.coarse_steps[Number(r.dataset.coarse)];r.classList.toggle("current-step",s.start_frame<=frame&&frame<s.end_frame);});
    if(active.length&&active.every(v=>v.readyState>=2&&!v.seeking)){
      const drift=Math.max(...active.map(v=>Math.abs(v.currentTime-(master?.currentTime||0))));
      $("review-sync").textContent=`${active.length}/3 路可用 · 视角时间差 ${(drift*1000).toFixed(0)} ms`;
    }
  }
  function downloadURL(format) {
    return "/api/annotation/viewer-file?"+new URLSearchParams({task,job:data.id,format,token});
  }
  async function exportViewer(format) {
    if(!data||pollingExport)return;
    const epoch=generation;pollingExport=true;$("review-export-download").hidden=true;
    const setStatus=result=>{
      if(epoch!==generation)return;
      $("review-export-status").textContent=result.message;
      if(result.state==="completed"){$("review-export-download").href=downloadURL(format);$("review-export-download").download=`episode_${data.episode_index}.${format}`;$("review-export-download").hidden=false;}
      if(result.state==="running")exportTimer=setTimeout(poll,1200);else pollingExport=false;
    };
    const poll=async()=>{try{setStatus(await api("/api/annotation/viewer-status?"+new URLSearchParams({task,job:data.id,format})));}catch(e){pollingExport=false;if(epoch===generation)$("review-export-status").textContent=e.message;}};
    try{setStatus(await api("/api/annotation/viewer-export",{task,job:data.id,format}));}
    catch(e){pollingExport=false;if(epoch===generation)$("review-export-status").textContent=e.message;}
  }
  function load(value,taskId) {
    clear();data=value;task=taskId;pollingExport=false;loopIndex=0;
    $("annotation-camera").value="observation.images.cam0";focus("cam0");
    const coarse=data.granularity?.coarse_steps||[], present=[...new Set(data.steps.map(s=>s.phase))];
    $("annotation-preview-info").textContent=`Episode ${data.episode_index} · ${data.recording_id} · ${data.model} · ${data.protocol||"历史协议"}`;
    $("review-summary").innerHTML=[["录制时长",clock(duration())],["细动作",data.steps.length+" 段"],["粗阶段",coarse.length+" 段"],["视频时间轴",data.fps+" fps · "+data.frames+" 帧"]].map(([label,value])=>`<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
    $("review-filter").innerHTML='<option value="">全部动作</option>'+present.map(p=>`<option value="${esc(p||"")}">${esc(phaseName(p))}</option>`).join("");
    $("annotation-coarse-info").textContent=`${coarse.length} 个粗阶段；相邻同类细动作合并。END 等未映射区间显示为灰色，不从细动作中删除。`;
    track("review-fine-track",data.steps);track("review-coarse-track",coarse,true);
    $("review-ruler").innerHTML=Array.from({length:5},(_,i)=>`<span>${clock(duration()*i/4)}</span>`).join("");
    $("review-legend").innerHTML=present.map(p=>`<span style="--phase-color:${color(p)}">${esc(phaseName(p))}</span>`).join("");
    $("review-scrubber").max=data.frames-1;$("review-scrubber").disabled=false;
    const epoch=generation;
    Object.entries(videoIds).forEach(([camera,id])=>{
      const v=$(id), key="observation.images."+camera;
      const available=data.camera_status?.[key]?.available ?? (data.video_available&&data.cameras.includes(key));
      if(!available){$("review-status-"+camera).textContent="源视频不可用";return;}
      active.push(v);if(!master)master=v;
      v.preload="auto";v.playbackRate=Number($("review-speed").value);
      v.onloadedmetadata=()=>{if(epoch!==generation)return;const ok=Math.abs(v.duration-duration())<=1/data.fps+.002;$("review-status-"+camera).textContent=ok?"已载入":"时长不匹配";if(!ok){active=active.filter(x=>x!==v);if(master===v)master=active[0]||null;v.pause();}$("review-play").disabled=!master;update(0);};
      v.oncanplay=v.onseeked=()=>{if(epoch===generation){update(currentFrame());reconcile();}};
      v.onwaiting=()=>{if(epoch===generation&&wantPlay){active.forEach(x=>x.pause());$("review-sync").textContent="正在缓冲 / 对齐三路视频…";}};
      v.onerror=()=>{if(epoch!==generation)return;v.pause();active=active.filter(x=>x!==v);if(master===v)master=active[0]||null;$("review-status-"+camera).textContent="读取失败";$("review-play").disabled=!master;reconcile();};
      v.onended=()=>{if(v!==master)return;if($("review-loop").checked&&data.steps[loopIndex]){seek(data.steps[loopIndex].start_frame);reconcile();}else pause();};
      v.src="/api/annotation/video?"+new URLSearchParams({task,job:data.id,camera:key,token});
    });
    tables();$("review-export-json").disabled=false;
    const sdk=data.viewer_sdks||{};
    for(const format of ["rrd","mcap"])$("review-export-"+format).disabled=!sdk[format];
    $("review-export-status").textContent=`Rerun ${sdk.rrd?"SDK 就绪":"SDK 未安装"} · Foxglove ${sdk.mcap?"SDK 就绪":"SDK 未安装"}。导出文件在本机生成，使用对应查看器打开。`;
  }
  function init() {
    $("annotation-camera").onchange=()=>focus($("annotation-camera").value);
    $("review-play").onclick=togglePlay;$("review-scrubber").oninput=()=>seek(Number($("review-scrubber").value),true);
    $("review-back").onclick=()=>{pause();seek(currentFrame()-1,true);};$("review-forward").onclick=()=>{pause();seek(currentFrame()+1,true);};
    $("review-prev").onclick=()=>{if(data)seek(data.steps[Math.max(0,currentIndex-1)].start_frame,true);};$("review-next").onclick=()=>{if(data)seek(data.steps[Math.min(data.steps.length-1,currentIndex+1)].start_frame,true);};
    $("review-speed").onchange=()=>active.forEach(v=>v.playbackRate=Number($("review-speed").value));
    $("review-loop").onchange=()=>{loopIndex=Math.max(0,currentIndex);};$("review-filter").onchange=tables;
    document.addEventListener("keydown",e=>{if(!data||$("view-preview").hidden||/INPUT|SELECT|TEXTAREA|BUTTON|A/.test(e.target.tagName)||e.ctrlKey||e.metaKey||e.altKey)return;if(e.code==="Space"){e.preventDefault();togglePlay();}if(["ArrowLeft","ArrowRight"].includes(e.code)){e.preventDefault();pause();seek(currentFrame()+(e.code==="ArrowRight"?1:-1),true);}});
    document.addEventListener("visibilitychange",()=>{if(document.hidden)pause();});
    for(const format of ["rrd","mcap"])$("review-export-"+format).onclick=()=>exportViewer(format);
    $("review-export-json").onclick=()=>{if(!data)return;const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download=`episode_${data.episode_index}_annotations.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  }
  return {init,load,clear,pause};
})();
