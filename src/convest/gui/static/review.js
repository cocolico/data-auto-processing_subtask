"use strict";
window.reviewUI = (() => {
  const phases = {
    GRASP_CLUSTER:["抓稳整串","#397be6"], PRESENT_CLUSTER:["抬至中央","#31a1c0"],
    APPROACH_AND_GRASP:["接近抓果","#9a70dd"], DETACH_AND_PLACE:["摘下放置","#e2a43a"],
    RETURN_AND_RELEASE:["放回松手","#36a783"], REORIENT_STEM:["调整番茄","#dc7586"], END:["低位静止","#8492a7"],
    HOLD_STABLE:["保持稳定","#8492a7"], SELECT_TARGET:["选择目标","#9a70dd"],
    APPROACH_TARGET:["接近目标","#9a70dd"], GRASP_ATTEMPT:["尝试抓取","#9a70dd"],
    RETRY_TARGET:["重试目标","#9a70dd"], DETACH_TARGET:["摘离番茄","#e2a43a"],
    PLACE_IN_TRAY:["放入果盘","#e2a43a"], ASSESS_REMAINING:["检查剩余","#8492a7"],
    RETURN_CLUSTER:["放回果串","#36a783"], REGRASP_AND_REORIENT:["重新抓握转向","#dc7586"],
    RECOVERY:["恢复动作","#8492a7"], IDLE:["等待","#8492a7"],
    PICK_UP:["拿起番茄","#397be6"], PICK_TOMATO:["摘取番茄","#e2a43a"],
    PUT_BACK:["放回番茄","#36a783"], ROTATE_STEM:["调整番茄","#dc7586"]
  };
  const fineTypes = {GRASP_CLUSTER:1,PRESENT_CLUSTER:2,APPROACH_AND_GRASP:3,
    DETACH_AND_PLACE:4,RETURN_AND_RELEASE:5,REORIENT_STEM:6,END:7};
  const coarseTypes = new Set(["PICK_UP","PICK_TOMATO","PUT_BACK","ROTATE_STEM"]);
  const coarseShort = {PICK_UP:"拿起",PICK_TOMATO:"摘取",PUT_BACK:"放回",ROTATE_STEM:"调整"};
  const instructionsZh = {
    GRASP_CLUSTER:"右手抓稳整串番茄。", PRESENT_CLUSTER:"右手将整串番茄抬至中央。",
    APPROACH_AND_GRASP:"右手保持果串稳定，左手接近并抓住一颗番茄。",
    DETACH_AND_PLACE:"右手保持果串稳定，左手摘下番茄并放入左侧果盘。",
    RETURN_AND_RELEASE:"右手将果串放回右侧白盘并松开。",
    REORIENT_STEM:"右手调整番茄的位置或朝向。", END:"双手在低位保持静止。"
  };
  const names = {cam0:"外部总览",cam1:"右手腕",cam2:"左手腕"};
  const videoIds = {cam0:"annotation-video",cam1:"annotation-video-cam1",cam2:"annotation-video-cam2"};
  let data=null, task=null, active=[], master=null, wantPlay=false, raf=0, generation=0, coarseSegments=[];
  let editing=false, editMenu=null, cancelDrag=null, sourceData=null, ignoreClickUntil=0;
  let saveDirectory="", directoryPath="", directoryParent="", directoryRequest=0;
  let currentIndex=-1, loopIndex=-1, exportTimer=0, pollingExport=false;
  const phaseName = s => phases[s]?.[0] || "未分类动作";
  const instruction = s => instructionsZh[s.phase] || phaseName(s.phase);
  const color = s => phases[s]?.[1] || "#8492a7";
  const duration = () => data ? data.frames/data.fps : 0;
  const timelineFrames = () => data ? Math.max(data.frames,60*data.fps) : 1;
  const frameAt = t => Math.max(0,Math.min((data?.frames||1)-1,Math.floor(t*(data?.fps||1)+0.001)));
  const clock = t => `${String(Math.floor(t/60)).padStart(2,"0")}:${(t%60).toFixed(3).padStart(6,"0")}`;
  const currentFrame = () => frameAt(master?.currentTime || 0);
  const segmentAt = frame => data?.steps.findIndex(s=>s.start_frame<=frame&&frame<s.end_frame) ?? -1;
  function pause() {
    wantPlay=false; active.forEach(v=>v.pause());cancelAnimationFrame(raf);
    $("review-play").textContent="播放";
  }
  function clear() {
    setEditing(false);sourceData=null;pause();generation++;clearTimeout(exportTimer);data=null;task=null;$("review-edit-start").disabled=true;$("review-import").disabled=true;active=[];master=null;currentIndex=-1;coarseSegments=[];
    $("review-timelines").style.minWidth="";$("review-timelines").style.width="";
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
  function layoutTimeline(){
    if(!data)return;
    const viewport=$("review-timeline-scroll").clientWidth;
    if(viewport<=60)return;
    // Keep 60 seconds in the visible track area, independent of segment lengths.
    const span=timelineFrames()/data.fps;
    $("review-timelines").style.width=(60+(viewport-60)*span/60)+"px";
    const ticks=[];
    for(let second=0;second<=span;second+=10)ticks.push(`<span style="left:${second/span*100}%">${clock(second).replace(".000","")}</span>`);
    $("review-ruler").innerHTML=ticks.join("");
  }
  function track(id, segments, coarse=false) {
    $(id).innerHTML=segments.map((s,i)=>`<button class="review-segment${coarse?"":" review-fine-segment"}" style="left:${s.start_frame/timelineFrames()*100}%;width:${(s.end_frame-s.start_frame)/timelineFrames()*100}%;--phase-color:${color(s.phase)}" data-start="${s.start_frame}" title="${coarse?"":"类型 "+(fineTypes[s.phase]||"?")+" · "}${esc(phaseName(s.phase))} · ${(s.start_frame/data.fps).toFixed(2)}–${(s.end_frame/data.fps).toFixed(2)} 秒" aria-label="${coarse?"粗阶段":"细动作类型 "+(fineTypes[s.phase]||"?")+"，第"+(i+1)+"段"}：${esc(phaseName(s.phase))}">${coarse?esc(coarseShort[s.phase]||phaseName(s.phase)):(fineTypes[s.phase]||"?")}</button>`).join("")+'<span class="review-playhead" aria-hidden="true"></span>';
    $(id).querySelectorAll("[data-start]").forEach((b,i)=>{
      b.onclick=()=>{if(Date.now()>ignoreClickUntil)seek(segments[i].start_frame,true);};
      b.oncontextmenu=e=>{if(!editing)return;e.preventDefault();showMenu(e,segments[i],coarse);};
      if(editing) for(const side of ["start","end"]){
        const handle=document.createElement("span");handle.className="review-resize "+side;
        handle.title=side==="start"?"拖动开始边界":"拖动结束边界";
        handle.onpointerdown=e=>beginDrag(e,segments,i,side,id);b.append(handle);
      }
    });
  }
  function tables() {
    if(!data)return;
    const filter=$("review-filter").value;
    $("annotation-steps").innerHTML=data.steps.map((s,i)=>filter&&s.phase!==filter?"":`<tr data-step="${i}"><td><button data-start="${s.start_frame}" aria-label="跳到动作 ${i+1}">${String(i+1).padStart(2,"0")}</button></td><td>${(s.start_frame/data.fps).toFixed(2)}–${(s.end_frame/data.fps).toFixed(2)} s<br><small>[${s.start_frame}, ${s.end_frame}) 帧</small></td><td><span class="review-phase" style="--phase-color:${color(s.phase)}">${fineTypes[s.phase]?fineTypes[s.phase]+" · ":""}${esc(phaseName(s.phase))}</span> ${s.target_id?`<span class="tag">${esc(s.target_id)}</span>`:""}<p>${esc(instruction(s))}</p></td></tr>`).join("");
    $("annotation-coarse-steps").innerHTML=coarseSegments.map((s,i)=>`<tr data-coarse="${i}"><td><button data-start="${s.start_frame}">${i+1}</button></td><td>${(s.start_frame/data.fps).toFixed(2)}–${(s.end_frame/data.fps).toFixed(2)}</td><td>${esc(phaseName(s.phase))}<br><small>细步骤 ${(s.fine_step_indices||[]).map(j=>j+1).join("、")}</small></td></tr>`).join("");
    ["annotation-steps","annotation-coarse-steps"].forEach(id=>$(id).querySelectorAll("[data-start]").forEach(b=>b.onclick=()=>seek(Number(b.dataset.start),true)));
    currentIndex=-1;update(currentFrame());
  }
  function inspect(index) {
    const s=data.steps[index];if(!s)return;
    const decisions={CONTINUE_PICK:"继续采摘",RETRY_SAME_TARGET:"重试当前目标",REGRASP:"重新抓握",STOP:"停止",UNKNOWN:"未知"};
    const coarse=coarseSegments.find(c=>c.start_frame<=s.start_frame&&s.start_frame<c.end_frame);
    $("review-inspector").innerHTML=`<span class="review-phase" style="--phase-color:${color(s.phase)}">${fineTypes[s.phase]?fineTypes[s.phase]+" · ":""}${esc(phaseName(s.phase))}</span><h3>动作 ${index+1} <small>/ ${data.steps.length}</small></h3><p class="review-instruction">${esc(instruction(s))}</p><dl><dt>细分类别</dt><dd>${fineTypes[s.phase]?fineTypes[s.phase]+" · ":""}${esc(phaseName(s.phase))}</dd><dt>粗阶段</dt><dd>${coarse?esc(phaseName(coarse.phase)):"—"}</dd><dt>起止时间</dt><dd>${(s.start_frame/data.fps).toFixed(3)}–${(s.end_frame/data.fps).toFixed(3)} s</dd><dt>帧范围</dt><dd>[${s.start_frame}, ${s.end_frame})</dd><dt>时长</dt><dd>${((s.end_frame-s.start_frame)/data.fps).toFixed(3)} s · ${s.end_frame-s.start_frame} 帧</dd><dt>目标番茄</dt><dd>${esc(s.target_id||"未指定 / 不确定")}</dd><dt>决策</dt><dd>${esc(s.decision?decisions[s.decision.choice]||s.decision.choice:"未标注")}${s.decision?.target_id?" · "+esc(s.decision.target_id):""}</dd><dt>放回时左侧番茄数</dt><dd>${s.return_condition?esc(s.return_condition.left_side_count??"被遮挡 / 未确定"):"不适用"}</dd></dl><button id="review-segment-start">回到本段起点</button><p class="hint">左侧数量按 cam0 中右手抓点的画面左侧计算。帧编号从 0 开始，右边界不包含在片段内。</p>`;
    $("review-segment-start").onclick=()=>seek(s.start_frame,true);
  }
  function update(frame) {
    if(!data)return;
    const time=master?.currentTime||frame/data.fps;
    $("review-time").textContent=`${clock(time)} / ${clock(duration())}`;
    $("review-frame").textContent=`帧 ${frame} / ${data.frames-1}`;$("review-scrubber").value=frame;
    document.querySelectorAll(".review-playhead").forEach(p=>p.style.left=`${frame/timelineFrames()*100}%`);
    const index=segmentAt(frame);
    if(index!==currentIndex){currentIndex=index;inspect(index);}
    $("annotation-steps").querySelectorAll("[data-step]").forEach(r=>r.classList.toggle("current-step",Number(r.dataset.step)===index));
    $("annotation-coarse-steps").querySelectorAll("[data-coarse]").forEach(r=>{const s=coarseSegments[Number(r.dataset.coarse)];r.classList.toggle("current-step",s.start_frame<=frame&&frame<s.end_frame);});
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
    clear();sourceData=structuredClone(value);data=structuredClone(value);task=taskId;pollingExport=false;loopIndex=0;
    $("annotation-camera").value="observation.images.cam0";focus("cam0");
    coarseSegments=(data.granularity?.coarse_steps||[]).filter(s=>coarseTypes.has(s.phase));
    const coarse=coarseSegments, present=[...new Set(data.steps.map(s=>s.phase))].sort((a,b)=>(fineTypes[a]||99)-(fineTypes[b]||99));
    $("annotation-preview-info").textContent=`Episode ${data.episode_index} · ${data.recording_id} · ${data.model} · ${data.protocol||"历史协议"}`+(data.notice?` · ${data.notice}`:"");
    $("annotation-preview-info").classList.toggle("review-draft-warning",!!data.trial);
    $("review-summary").innerHTML=[["录制时长",clock(duration())],["细动作",data.steps.length+" 段"],["粗阶段",coarse.length+" 段"],["视频时间轴",data.fps+" fps · "+data.frames+" 帧"]].map(([label,value])=>`<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
    $("review-filter").innerHTML='<option value="">全部动作</option>'+present.map(p=>`<option value="${esc(p||"")}">${esc(phaseName(p))}</option>`).join("");
    $("annotation-coarse-info").textContent=`${coarse.length} 段粗阶段，仅使用拿起番茄、摘取番茄、放回番茄、调整番茄四类动作。`;
    layoutTimeline();
    track("review-fine-track",data.steps);track("review-coarse-track",coarse,true);

    $("review-legend").innerHTML='<strong>粗阶段</strong>'+["PICK_UP","PICK_TOMATO","PUT_BACK","ROTATE_STEM"].map(p=>`<span style="--phase-color:${color(p)}">${esc(phaseName(p))}</span>`).join("")+
      '<strong>细动作类型</strong>'+present.map(p=>`<span style="--phase-color:${color(p)}">${fineTypes[p]?fineTypes[p]+" · ":""}${esc(phaseName(p))}</span>`).join("");
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
      v.src=data.trial ? "/api/annotation/trial-video?"+new URLSearchParams({run:data.id.slice(6),camera,token}) :
        "/api/annotation/video?"+new URLSearchParams({task,job:data.id,camera:key,token});
    });
    alignLayers();redraw();$("review-export-json").disabled=false;$("review-edit-start").disabled=false;$("review-import").disabled=false;
    const sdk=data.viewer_sdks||{};
    for(const format of ["rrd","mcap"])$("review-export-"+format).disabled=!sdk[format];
    $("review-export-status").textContent=data.trial ? data.notice :
      `Rerun ${sdk.rrd?"SDK 就绪":"SDK 未安装"} · Foxglove ${sdk.mcap?"SDK 就绪":"SDK 未安装"}。导出文件在本机生成，使用对应查看器打开。`;
  }
  const englishText = {
    GRASP_CLUSTER:"Grasp the tomato truss securely with the right hand.",
    PRESENT_CLUSTER:"Lift the tomato truss to the center with the right hand.",
    APPROACH_AND_GRASP:"Approach and grasp one tomato with the left hand while holding the truss steady with the right hand.",
    DETACH_AND_PLACE:"Detach the tomato and place it in the left tray with the left hand while holding the truss steady with the right hand.",
    RETURN_AND_RELEASE:"Return the tomato truss to the right white plate and release it with the right hand.",
    REORIENT_STEM:"Adjust the tomato truss using the right hand.",
    PICK_UP:"Pick up the tomato truss.", PICK_TOMATO:"Pick tomatoes and place them in the left tray.",
    PUT_BACK:"Put back the tomato truss.", ROTATE_STEM:"Adjust the tomato truss."
  };
  function splitFineAt(frame){
    const i=data.steps.findIndex(s=>s.start_frame<frame&&frame<s.end_frame);
    if(i<0)return;
    const left=data.steps[i], right=structuredClone(left);right.start_frame=frame;right.decision=null;
    left.end_frame=frame;data.steps.splice(i+1,0,right);
  }
  function alignLayers(){
    for(const c of coarseSegments){splitFineAt(c.start_frame);splitFineAt(c.end_frame);}
    coarseSegments.forEach(c=>{c.fine_step_indices=data.steps.flatMap((f,i)=>f.start_frame<c.end_frame&&f.end_frame>c.start_frame?[i]:[]);});
  }
  function validateAlignment(){
    let cursor=0;
    for(const f of data.steps){if(!Number.isInteger(f.start_frame)||!Number.isInteger(f.end_frame)||f.start_frame!==cursor||f.end_frame<=cursor||f.end_frame>data.frames)throw Error("细动作时间轴不连续");cursor=f.end_frame;}
    if(cursor!==data.frames)throw Error("细动作未覆盖视频");
    cursor=0;
    for(const c of coarseSegments){const children=c.fine_step_indices.map(i=>data.steps[i]);
      if(c.start_frame<cursor||!children.length||children[0].start_frame!==c.start_frame||children.at(-1).end_frame!==c.end_frame)throw Error("粗细动作边界未严格对齐");cursor=c.end_frame;
    }
    for(const f of data.steps){const parents=coarseSegments.filter(c=>c.start_frame<=f.start_frame&&f.end_frame<=c.end_frame);
      if(parents.length!==(f.phase==="END"?0:1))throw Error("细动作必须完整对应一个粗动作，结束静止除外");}
  }
  function paintCoarse(start,end,phase){
    const rows=[];
    for(const c of coarseSegments){
      if(c.end_frame<=start||c.start_frame>=end){rows.push(c);continue;}
      if(c.start_frame<start)rows.push({...structuredClone(c),end_frame:start});
      if(c.end_frame>end)rows.push({...structuredClone(c),start_frame:end});
    }
    if(phase)rows.push({start_frame:start,end_frame:end,phase,text:englishText[phase],fine_step_indices:[]});
    coarseSegments=rows.sort((a,b)=>a.start_frame-b.start_frame);
    // Preserve explicit split boundaries outside the newly painted interval.
    for(let i=coarseSegments.length-1;i>0;i--){const a=coarseSegments[i-1],b=coarseSegments[i];
      if(a.end_frame===b.start_frame&&a.phase===b.phase&&(a.end_frame===start||a.end_frame===end)){a.end_frame=b.end_frame;coarseSegments.splice(i,1);}}
  }
  function closeMenu(){editMenu?.remove();editMenu=null;}
  function redraw(){
    alignLayers();
    const counts=$("review-summary").querySelectorAll("strong");
    if(counts.length>=3){counts[1].textContent=data.steps.length+" 段";counts[2].textContent=coarseSegments.length+" 段";}
    layoutTimeline();
    loopIndex=Math.max(0,segmentAt(currentFrame()));
    track("review-fine-track",data.steps);track("review-coarse-track",coarseSegments,true);tables();
  }
  function setEditing(enabled){
    cancelDrag?.();closeMenu();editing=enabled;
    $("review-edit-start").disabled=enabled||!data;$("review-edit-stop").disabled=!enabled;
    $("review-save-local").disabled=!enabled;$("review-save-local").classList.toggle("primary",enabled);
    $("review-edit-status").textContent=enabled?"修改模式：拖动边缘调整时间，右键修改类别；仅修改当前副本。":"修改已锁定；再次开启可继续编辑。";
    $("review-timelines").classList.toggle("editing",enabled);
    if(data)redraw();
  }
  function showMenu(e,segment,coarse){
    closeMenu();editMenu=document.createElement("div");editMenu.className="review-context-menu";editMenu.setAttribute("role","menu");
    const options=coarse?[...coarseTypes]:Object.keys(fineTypes).filter(p=>fineTypes[p]<=6);
    for(const phase of options){const b=document.createElement("button");b.textContent=(coarse?"":fineTypes[phase]+" · ")+phaseName(phase);b.setAttribute("role","menuitem");
      b.onclick=()=>{if(!editing)return;segment.phase=phase;segment.text=englishText[phase];
        if(!coarse){segment.decision=null;segment.target_id=null;segment.return_condition=phase==="RETURN_AND_RELEASE"?{left_side_count:null}:null;}
        if(!coarse&&!coarseSegments.some(c=>c.start_frame<=segment.start_frame&&segment.end_frame<=c.end_frame)){
          const category={GRASP_CLUSTER:"PICK_UP",PRESENT_CLUSTER:"PICK_UP",APPROACH_AND_GRASP:"PICK_TOMATO",DETACH_AND_PLACE:"PICK_TOMATO",RETURN_AND_RELEASE:"PUT_BACK",REORIENT_STEM:"ROTATE_STEM"}[phase];
          paintCoarse(segment.start_frame,segment.end_frame,category);
        }
        closeMenu();redraw();};editMenu.append(b);}
    const segments=coarse?coarseSegments:data.steps, index=segments.indexOf(segment);
    const addAction=(label,enabled,action)=>{const b=document.createElement("button");b.textContent=label;b.disabled=!enabled;b.setAttribute("role","menuitem");b.onclick=()=>{if(!editing)return;pause();action();closeMenu();redraw();};editMenu.append(b);};
    editMenu.append(document.createElement("hr"));
    addAction("中分",segment.end_frame-segment.start_frame>=2,()=>{
      const midpoint=segment.start_frame+Math.floor((segment.end_frame-segment.start_frame)/2);
      const second=structuredClone(segment);second.start_frame=midpoint;
      if(!coarse)second.decision=null;
      segment.end_frame=midpoint;segments.splice(index+1,0,second);if(coarse)splitFineAt(midpoint);seek(midpoint,true);
      $("review-edit-status").textContent=`已从 ${midpoint} 帧中分为两段，可分别修改类别。`;
    });
    const merge=neighborIndex=>{
      const neighbor=segments[neighborIndex];
      const parent=coarseSegments.find(c=>c.start_frame<=segment.start_frame&&segment.end_frame<=c.end_frame);
      const merged={...structuredClone(segment),start_frame:Math.min(segment.start_frame,neighbor.start_frame),end_frame:Math.max(segment.end_frame,neighbor.end_frame)};
      if(!coarse){merged.target_id=null;merged.decision=null;merged.return_condition=merged.phase==="RETURN_AND_RELEASE"?{left_side_count:null}:null;}
      segments.splice(Math.min(index,neighborIndex),2,merged);
      if(!coarse)paintCoarse(merged.start_frame,merged.end_frame,merged.phase==="END"?null:parent?.phase);
      seek(merged.start_frame,true);
      $("review-edit-status").textContent="已融合相邻动作块，保留当前右键动作块的类别。";
    };
    addAction("向左融合",index>0&&segments[index-1].end_frame===segment.start_frame,()=>merge(index-1));
    addAction("向右融合",index<segments.length-1&&segment.end_frame===segments[index+1].start_frame,()=>merge(index+1));
    document.body.append(editMenu);const rect=editMenu.getBoundingClientRect();
    editMenu.style.left=Math.max(0,Math.min(e.clientX,innerWidth-rect.width))+"px";
    editMenu.style.top=Math.max(0,Math.min(e.clientY,innerHeight-rect.height))+"px";
  }
  function beginDrag(e,segments,index,side,id){
    if(!editing||e.button!==0)return;e.preventDefault();e.stopPropagation();pause();closeMenu();
    const s=segments[index], previous=segments[index-1], next=segments[index+1];
    const shared=side==="start"?previous?.end_frame===s.start_frame:next?.start_frame===s.end_frame;
    const rect=$(id).getBoundingClientRect(), handle=e.currentTarget;
    const coarse=segments===coarseSegments, originalBoundary=side==="start"?s.start_frame:s.end_frame;
    const linked=coarse?[]:coarseSegments.flatMap(c=>["start_frame","end_frame"].filter(key=>c[key]===originalBoundary).map(key=>({c,key})));
    const originalFine=coarse?structuredClone(data.steps):null;
    let min=side==="start"?(shared?previous.start_frame+1:previous?.end_frame??0):s.start_frame+1;
    let max=side==="start"?s.end_frame-1:(shared?next.end_frame-1:next?.start_frame??data.frames);
    const edgeLinks=coarse&&!shared?originalFine.flatMap((f,i)=>["start_frame","end_frame"].filter(key=>f[key]===originalBoundary).map(key=>({i,key}))):[];
    for(const {i,key} of edgeLinks){if(key==="end_frame")min=Math.max(min,originalFine[i].start_frame+1);else max=Math.min(max,originalFine[i].end_frame-1);}
    // Keep outer episode boundaries fixed; shared boundaries resize both neighbors.
    if(side==="start"&&s.start_frame===0||side==="end"&&s.end_frame===data.frames)return;
    handle.setPointerCapture(e.pointerId);
    let previewFrame=side==="start"?s.start_frame:s.end_frame, previewRequest=0;
    seek(previewFrame,true);
    const move=event=>{if(!editing)return;const frame=Math.max(min,Math.min(max,Math.round((event.clientX-rect.left)/rect.width*timelineFrames())));
      if(side==="start"){s.start_frame=frame;if(shared)previous.end_frame=frame;}
      else{s.end_frame=frame;if(shared)next.start_frame=frame;}
      if(coarse){data.steps=structuredClone(originalFine);edgeLinks.forEach(({i,key})=>{data.steps[i][key]=frame;});for(const c of coarseSegments){splitFineAt(c.start_frame);splitFineAt(c.end_frame);}track("review-fine-track",data.steps);}
      else{linked.forEach(({c,key})=>{c[key]=frame;});
        [...$("review-coarse-track").querySelectorAll(".review-segment")].forEach((b,i)=>{b.style.left=coarseSegments[i].start_frame/timelineFrames()*100+"%";b.style.width=(coarseSegments[i].end_frame-coarseSegments[i].start_frame)/timelineFrames()*100+"%";});}
      [...$(id).querySelectorAll(".review-segment")].forEach((b,i)=>{b.style.left=segments[i].start_frame/timelineFrames()*100+"%";b.style.width=(segments[i].end_frame-segments[i].start_frame)/timelineFrames()*100+"%";});
      previewFrame=frame;
      // Coalesce pointer events while always seeking the latest boundary in all views.
      if(!previewRequest)previewRequest=requestAnimationFrame(()=>{previewRequest=0;seek(previewFrame,true);});
      $("review-edit-status").textContent=`边界：${frame} 帧 · ${(frame/data.fps).toFixed(3)} 秒`;
    };
    const finish=()=>{handle.removeEventListener("pointermove",move);handle.removeEventListener("pointerup",finish);handle.removeEventListener("pointercancel",finish);cancelAnimationFrame(previewRequest);ignoreClickUntil=Date.now()+300;cancelDrag=null;seek(previewFrame,true);redraw();};
    cancelDrag=finish;handle.addEventListener("pointermove",move);handle.addEventListener("pointerup",finish);handle.addEventListener("pointercancel",finish);
  }
  function savedCopy(manual=false){
    alignLayers();validateAlignment();
    const saved=structuredClone(data);
    delete saved.notice;delete saved.camera_status;delete saved.viewer_sdks;
    saved.granularity=JSON.parse(JSON.stringify(saved.granularity||{},(k,v)=>k==="text_zh"?undefined:v));
    saved.granularity.fine_steps=structuredClone(data.steps);
    saved.granularity.excluded_intervals=data.steps.flatMap((s,i)=>fineTypes[s.phase]<=6?[]:[{fine_step_index:i}]);
    saved.granularity.coarse_steps=structuredClone(coarseSegments).map(s=>({...s,fine_step_indices:data.steps.flatMap((f,i)=>f.start_frame<s.end_frame&&f.end_frame>s.start_frame?[i]:[])}));
    if(manual){saved.manual_review={schema_version:"manual-annotation-copy-v1",saved_at:new Date().toISOString(),source_job_id:sourceData?.id,independent_tracks:false,boundaries_linked:true};saved.granularity.method="manual_linked_boundaries";}
    return saved;
  }
  function downloadCopy(saved,name){const url=URL.createObjectURL(new Blob([JSON.stringify(saved,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function browseSaveDirectory(path){
    const request=++directoryRequest;$("review-directory-select").disabled=true;$("review-directory-error").textContent="";
    try{const result=await api("/api/browse",{path});if(request!==directoryRequest)return;
      directoryPath=result.path;directoryParent=result.parent;$("review-directory-path").value=directoryPath;
      $("review-directory-entries").replaceChildren();
      for(const entry of result.entries){const button=document.createElement("button");button.textContent=entry.name+" /";button.onclick=()=>browseSaveDirectory(entry.path);$("review-directory-entries").append(button);}
      $("review-directory-select").disabled=false;
    }catch(e){if(request===directoryRequest)$("review-directory-error").textContent=e.message;}
  }
  function chooseSaveDirectory(){
    $("review-directory-dialog").showModal();$("review-directory-path").value=saveDirectory;
    browseSaveDirectory(saveDirectory);
  }
  async function saveLocal(){
    if(!editing||!data)return;
    if(!saveDirectory){chooseSaveDirectory();$("review-edit-status").textContent="请先选择本地保存目录；之后无需重复选择。";return;}
    $("review-save-local").disabled=true;
    try{const result=await api("/api/annotation/manual-save",{directory:saveDirectory,payload:savedCopy(true)});
      $("review-edit-status").textContent="人工修改副本已保存："+result.path;
    }catch(e){$("review-edit-status").textContent="保存失败："+e.message;}
    finally{$("review-save-local").disabled=!editing;}
  }
  async function importCopy(file){
    if(!file||!data)return;
    try{const imported=JSON.parse(await file.text());
      if(imported.frames!==data.frames||imported.fps!==data.fps||imported.recording_id!==data.recording_id||imported.episode_index!==data.episode_index)throw Error("请导入当前录制和 Episode 对应的标注文件");
      const validate=(rows,allowed,full)=>{if(!Array.isArray(rows)||!rows.length)throw Error("缺少动作数据");let cursor=0;for(const s of rows){if(!Number.isInteger(s.start_frame)||!Number.isInteger(s.end_frame)||s.start_frame<cursor||s.start_frame>=s.end_frame||s.end_frame>data.frames||!allowed.has(s.phase)||full&&s.start_frame!==cursor)throw Error("动作类别或时间范围无效");cursor=s.end_frame;}if(full&&cursor!==data.frames)throw Error("细动作未覆盖完整视频");};
      validate(imported.steps,new Set(Object.keys(fineTypes)),true);validate(imported.granularity?.coarse_steps,coarseTypes,false);
      const oldFine=data.steps,oldCoarse=coarseSegments;
      try{data.steps=structuredClone(imported.steps);coarseSegments=structuredClone(imported.granularity.coarse_steps);alignLayers();validateAlignment();}
      catch(e){data.steps=oldFine;coarseSegments=oldCoarse;throw e;}
      setEditing(false);
      $("review-edit-status").textContent="已导入独立副本，点击开启修改模式继续审核。";
    }catch(e){$("review-edit-status").textContent="导入失败："+e.message;}
  }
  function init() {
    new ResizeObserver(layoutTimeline).observe($("review-timeline-scroll"));
    $("annotation-camera").onchange=()=>focus($("annotation-camera").value);
    $("review-play").onclick=togglePlay;$("review-scrubber").oninput=()=>seek(Number($("review-scrubber").value),true);
    $("review-back").onclick=()=>{pause();seek(currentFrame()-1,true);};$("review-forward").onclick=()=>{pause();seek(currentFrame()+1,true);};
    $("review-prev").onclick=()=>{if(data)seek(data.steps[Math.max(0,currentIndex-1)].start_frame,true);};$("review-next").onclick=()=>{if(data)seek(data.steps[Math.min(data.steps.length-1,currentIndex+1)].start_frame,true);};
    $("review-speed").onchange=()=>active.forEach(v=>v.playbackRate=Number($("review-speed").value));
    $("review-loop").onchange=()=>{loopIndex=Math.max(0,currentIndex);};$("review-filter").onchange=tables;
    document.addEventListener("keydown",e=>{if(!data||$("view-preview").hidden||/INPUT|SELECT|TEXTAREA|BUTTON|A/.test(e.target.tagName)||e.ctrlKey||e.metaKey||e.altKey)return;if(e.code==="Space"){e.preventDefault();togglePlay();}if(["ArrowLeft","ArrowRight"].includes(e.code)){e.preventDefault();pause();seek(currentFrame()+(e.code==="ArrowRight"?1:-1),true);}});
    document.addEventListener("visibilitychange",()=>{if(document.hidden)pause();});
    for(const format of ["rrd","mcap"])$("review-export-"+format).onclick=()=>exportViewer(format);
    $("review-edit-start").onclick=()=>setEditing(true);$("review-edit-stop").onclick=()=>setEditing(false);
    $("review-save-local").onclick=saveLocal;
    try{saveDirectory=localStorage.getItem("manual-review-save-directory")||"";}catch(e){}
    $("review-save-path").value=saveDirectory;
    $("review-save-directory").onclick=chooseSaveDirectory;
    $("review-directory-go").onclick=()=>browseSaveDirectory($("review-directory-path").value);
    $("review-directory-path").onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();browseSaveDirectory(e.target.value);}};
    $("review-directory-up").onclick=()=>browseSaveDirectory(directoryParent);
    $("review-directory-cancel").onclick=()=>$("review-directory-dialog").close();
    $("review-directory-select").onclick=()=>{saveDirectory=directoryPath;$("review-save-path").value=saveDirectory;try{localStorage.setItem("manual-review-save-directory",saveDirectory);}catch(e){}$("review-directory-dialog").close();$("review-edit-status").textContent="已选择保存目录，后续保存直接更新该目录中的人工副本。";};
    $("review-import").onclick=()=>$("review-import-file").click();
    $("review-import-file").onchange=async e=>{await importCopy(e.target.files[0]);e.target.value="";};
    document.addEventListener("pointerdown",e=>{if(editMenu&&!editMenu.contains(e.target))closeMenu();});
    document.addEventListener("keydown",e=>{if(e.key==="Escape")closeMenu();});
    $("review-export-json").onclick=()=>{try{if(data)downloadCopy(savedCopy(true),`episode_${data.episode_index}_annotations.json`);}catch(e){$("review-edit-status").textContent="导出失败："+e.message;}};
  }
  return {init,load,clear,pause};
})();
