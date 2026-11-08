"use strict";
window.reviewUI = (() => {
  const categories = [
    ["FIRST_LIFT","第一次拿起","Right hand grasps and lifts the truss to the center"],
    ["FIRST_PICK","第一次采摘投放","Right hand holds truss while left hand picks tomatoes into left basket"],
    ["RETURN_TRUSS","放回番茄串","Right hand returns the truss to the right plate"],
    ["REPOSITION","调整番茄","Right fingers reposition the truss on the right plate"],
    ["SECOND_LIFT","第二次拿起","Right hand grasps and lifts the truss to the center"],
    ["SECOND_PICK","继续采摘投放","Right hand holds truss while left hand picks tomatoes into left basket"],
    ["RETURN_BARE_STEM","放回裸杆","Right hand returns the bare stem to the right plate"]
  ];
  const colors=["#397be6","#9a70dd","#36a783","#dc7586","#31a1c0","#e2a43a","#677a9e"];
  const videoIds=["annotation-video","annotation-video-cam1","annotation-video-cam2"];
  let data=null,task=null,active=[],master=null,playing=false,raf=0,editing=false,selectedStage=0;
  let history=[],modified=false,epoch=0,dragging=false,exportTimer=0;
  const message=t=>$("review-edit-status").textContent=t;
  const clock=t=>`${Math.floor(t/60)}:${(t%60).toFixed(3).padStart(6,"0")}`;
  const frame=()=>Math.max(0,Math.min((data?.frames||1)-1,Math.floor((master?.currentTime||0)*(data?.fps||1)+0.001)));
  const name=(s,i)=>categories.find(c=>c[0]===s.phase)?.[1]||s.phase||`阶段 ${i+1}`;
  const valid=()=>{
    if(!data||data.steps.length!==7)throw Error("必须有且仅有七阶段；缺失动作的视频请标记不合格。");
    let cursor=0;
    data.steps.forEach((s,i)=>{if(s.phase!==categories[i][0]||s.stage_index!==i||s.text!==categories[i][2]||!Number.isInteger(s.start_frame)||!Number.isInteger(s.end_frame)||s.start_frame!==cursor||!(cursor<s.end_frame&&s.end_frame<=data.frames))throw Error("七阶段顺序、文字或帧边界无效");cursor=s.end_frame;});
    if(cursor!==data.frames)throw Error("未覆盖全部视频帧");
  };
  function pause(){playing=false;active.forEach(v=>v.pause());cancelAnimationFrame(raf);$("review-play").textContent="播放";}
  function controls(){
    const writable=!!data&&!data.readonly&&data.single_track;
    $("review-edit-start").disabled=!writable||editing;$("review-edit-stop").disabled=!editing;
    $("review-save-local").disabled=!writable||!editing;$("review-approve").disabled=!writable;
    $("review-reject").disabled=!writable;$("review-undo").disabled=!editing||!history.length;
    $("review-import").disabled=!writable;$("review-reopen").disabled=!data?.can_reopen;
    $("review-initialize").disabled=!writable||data.steps.length===7||data.frames<7;
    $("review-export-json").disabled=!data;
    $("review-timelines").classList.toggle("editing",editing);
  }
  function clear(){
    pause();clearTimeout(exportTimer);epoch++;data=null;task=null;editing=false;history=[];modified=false;selectedStage=0;active=[];master=null;
    $("review-export-download").hidden=true;
    videoIds.forEach((id,i)=>{const v=$(id);v.onloadedmetadata=v.oncanplay=v.onseeked=v.onended=v.onerror=null;v.removeAttribute("src");v.load();$("review-status-cam"+i).textContent="未载入";});
    ["review-fine-track","review-coarse-track","annotation-steps","annotation-coarse-steps","review-summary","review-legend","review-ruler"].forEach(id=>$(id).replaceChildren());
    $("review-inspector").textContent="打开结果后检查阶段与帧边界。";$("review-play").disabled=true;controls();
  }
  function seek(n,choose=false){if(!data)return;n=Math.max(0,Math.min(data.frames-1,Math.round(n)));active.forEach(v=>{if(v.readyState>=1)v.currentTime=n/data.fps;});if(choose){const i=data.steps.findIndex(s=>s.start_frame<=n&&n<s.end_frame);if(i>=0)selectedStage=i;}update(n);}
  function update(n=frame()){
    if(!data)return;
    $("review-time").textContent=`${clock(n/data.fps)} / ${clock(data.frames/data.fps)}`;
    const source=data.source_frame_index?.[n];
    $("review-frame").textContent=`帧 ${n} / ${data.frames-1}`+(source!=null?` · 源帧 ${Math.round(source)}`:"");
    $("review-scrubber").value=n;
    $("review-sync").textContent=`${active.filter(v=>v.readyState>=1).length}/3 路已载入，同步按当前帧检查`;
    $("review-fine-track").querySelectorAll(".review-segment").forEach((b,i)=>b.classList.toggle("current",data.steps[i].start_frame<=n&&n<data.steps[i].end_frame));
    $("review-fine-track").querySelector(".review-playhead")?.style.setProperty("left",`${n/data.frames*100}%`);
    const s=data.steps[selectedStage];
    if(s)$("review-inspector").innerHTML=`<h3>${esc(name(s,selectedStage))}</h3><p>${esc(s.text)}</p><p>[${s.start_frame}, ${s.end_frame}) 帧</p><p>${((s.end_frame-s.start_frame)/data.fps).toFixed(3)} 秒</p><p class="hint">阶段编号固定；拖动相邻块的共享边界或在表格输入帧号。右边界不包含在本段内。</p>`;
  }
  async function play(){
    if(!master)return;
    if(playing){pause();return;}
    playing=true;const expected=epoch,videos=[...active];$("review-play").textContent="暂停";
    try{await Promise.all(videos.map(v=>v.play()));}catch(e){pause();message("视频未就绪，请稍后重试播放。");return;}
    if(expected!==epoch||!playing){videos.forEach(v=>v.pause());return;}
    const tick=()=>{if(!playing||!data)return;const n=frame(),s=data.steps[selectedStage];
      if($("review-loop").checked&&s&&master.currentTime>=s.end_frame/data.fps)seek(s.start_frame);
      active.filter(v=>v!==master&&v.readyState>=2&&!v.seeking).forEach(v=>{if(Math.abs(v.currentTime-master.currentTime)>1/data.fps)v.currentTime=master.currentTime;});
      update();raf=requestAnimationFrame(tick);};tick();
  }
  function remember(){history.push(structuredClone(data.steps));if(history.length>100)history.shift();modified=true;controls();}
  function boundary(i,value){
    if(!editing||i<1||i>6)return;
    const n=Number(value),left=data.steps[i-1],right=data.steps[i];
    if(!Number.isInteger(n)||n<=left.start_frame||n>=right.end_frame)throw Error("边界必须为整数，且相邻两阶段都至少保留一帧。");
    left.end_frame=n;right.start_frame=n;valid();
  }
  function layout(){
    if(!data)return;
    const viewport=$("review-timeline-scroll").clientWidth||800;
    $("review-timelines").style.width=Math.max(viewport,(viewport-60)*data.frames/data.fps/60+60)+"px";
    const seconds=data.frames/data.fps,ticks=[];
    for(let t=0;t<=seconds;t+=10)ticks.push(`<span style="left:${t/seconds*100}%">${clock(t)}</span>`);
    $("review-ruler").innerHTML=ticks.join("");
  }
  function draw(){
    if(!data)return;layout();controls();
    $("review-fine-track").innerHTML=data.steps.map((s,i)=>`<button class="review-segment" style="left:${s.start_frame/data.frames*100}%;width:${(s.end_frame-s.start_frame)/data.frames*100}%;--phase-color:${colors[i%7]}" title="${esc(name(s,i))} [${s.start_frame},${s.end_frame})" aria-label="阶段 ${i+1}：${esc(name(s,i))}">${i+1}</button>`).join("")+'<span class="review-playhead"></span>';
    $("review-fine-track").querySelectorAll(".review-segment").forEach((b,i)=>{
      b.onclick=()=>{if(dragging)return;selectedStage=i;seek(data.steps[i].start_frame);};
      if(editing&&i>0){const handle=document.createElement("span");handle.className="review-resize start";handle.title="拖动共享边界";handle.onpointerdown=e=>{
        e.preventDefault();e.stopPropagation();pause();remember();dragging=true;handle.setPointerCapture(e.pointerId);
        const rect=$("review-fine-track").getBoundingClientRect();
        const move=event=>{const lo=data.steps[i-1].start_frame+1,hi=data.steps[i].end_frame-1,n=Math.max(lo,Math.min(hi,Math.round((event.clientX-rect.left)/rect.width*data.frames)));boundary(i,n);
          $("review-fine-track").querySelectorAll(".review-segment").forEach((v,j)=>{v.style.left=data.steps[j].start_frame/data.frames*100+"%";v.style.width=(data.steps[j].end_frame-data.steps[j].start_frame)/data.frames*100+"%";});seek(n);message(`共享边界 ${n} 帧 · ${(n/data.fps).toFixed(3)} 秒，尚未保存`);};
        const finish=()=>{handle.removeEventListener("pointermove",move);handle.removeEventListener("pointerup",finish);handle.removeEventListener("pointercancel",finish);dragging=false;draw();};
        handle.addEventListener("pointermove",move);handle.addEventListener("pointerup",finish);handle.addEventListener("pointercancel",finish);
      };b.append(handle);}
    });
    $("annotation-steps").innerHTML=data.steps.map((s,i)=>$("review-filter").value&&$("review-filter").value!==s.phase?"":`<tr><td><button data-step="${i}">${i+1}</button></td><td>[${i===0?s.start_frame:`<input class="stage-boundary" data-boundary="${i}" type="number" value="${s.start_frame}" ${editing?"":"disabled"} aria-label="阶段 ${i+1} 开始帧">`}, ${s.end_frame}) 帧<br>${(s.start_frame/data.fps).toFixed(3)}–${(s.end_frame/data.fps).toFixed(3)} s</td><td>${esc(name(s,i))}<p>${esc(s.text)}</p></td></tr>`).join("");
    $("annotation-steps").querySelectorAll("[data-step]").forEach(b=>b.onclick=()=>{selectedStage=Number(b.dataset.step);seek(data.steps[selectedStage].start_frame);});
    $("annotation-steps").querySelectorAll("[data-boundary]").forEach(b=>b.onchange=()=>{try{remember();boundary(Number(b.dataset.boundary),b.value);message("边界已修改，尚未保存");}catch(e){message(e.message);}draw();});
    $("review-legend").innerHTML=data.steps.map((s,i)=>`<span style="--phase-color:${colors[i%7]}">${i+1} · ${esc(name(s,i))}</span>`).join("");
    update();
  }
  function load(value,taskId){
    clear();data=structuredClone(value);task=taskId;const current=epoch;
    $("annotation-preview-info").textContent=`${data.variant||"draft"} · ${data.source_label||"原始编号待确认"} · ${data.review_state||"历史结果"} · 版本 ${data.revision||0}`;
    $("review-summary").innerHTML=[["录制时长",clock(data.frames/data.fps)],["动作阶段",data.steps.length+" / 7"],["数据版本",data.variant||"draft"],["视频时间轴",`${data.fps} fps · ${data.frames} 帧`]].map(([k,v])=>`<div><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join("");
    $("review-filter").innerHTML='<option value="">全部阶段</option>'+data.steps.map((s,i)=>`<option value="${esc(s.phase)}">${esc(name(s,i))}</option>`).join("");
    $("review-storage-path").textContent=data.review_storage?`人工修改保存在：${data.review_storage.revisions}（正式审核队列同步保存；整轮放行后生成 LeRobot 成品）`:"";
    $("review-scrubber").max=data.frames-1;$("review-scrubber").disabled=false;
    videoIds.forEach((id,i)=>{const v=$(id),key="observation.images.cam"+i;
      if(!data.camera_status?.[key]?.available){$("review-status-cam"+i).textContent="视频不可用";return;}
      active.push(v);if(!master)master=v;
      v.playbackRate=Number($("review-speed").value);
      v.onloadedmetadata=()=>{if(current!==epoch)return;const ok=Math.abs(v.duration-data.frames/data.fps)<=1/data.fps+.002;$("review-status-cam"+i).textContent=ok?"已载入":"时长不匹配";if(!ok){active=active.filter(x=>x!==v);if(master===v)master=active[0]||null;}$("review-play").disabled=!master;update();};
      v.onerror=()=>{$("review-status-cam"+i).textContent="读取失败";};
      v.onended=()=>{if(v!==master)return;if($("review-loop").checked&&data.steps[selectedStage]){seek(data.steps[selectedStage].start_frame);active.forEach(x=>x.play().catch(()=>pause()));}else pause();};
      v.src=data.trial?"/api/annotation/trial-video?"+new URLSearchParams({run:data.id.slice(6),camera:"cam"+i,token}):"/api/annotation/video?"+new URLSearchParams({task,job:data.id,camera:key,variant:data.variant||"draft",token});
    });
    message(data.readonly?"只读结果；审核通过后的版本已冻结。":data.steps.length===7?"检查七阶段后开启修改模式；保存草稿不会放行。":"模型未产生完整七阶段。可建立人工草稿；真实缺少阶段则标记不合格。");
    $("review-export-rrd").disabled=!data.viewer_sdks?.rrd;$("review-export-mcap").disabled=!data.viewer_sdks?.mcap;$("review-export-status").textContent="可下载单层七阶段 JSON。正式标签由审核通过和批次放行生成。";
    $("review-import").disabled=!!data.readonly;draw();
  }
  async function submit(action){
    if(!data||(data.readonly&&action!=="reopen"))return;
    if(!["reject","reopen"].includes(action))valid();
    const reason=action==="reject"?$("review-reject-reason").value:"";
    const expected=epoch,identity=data.id,currentTask=task;
    const result=await api("/api/annotation/review",{task:currentTask,job:identity,revision:data.revision,steps:data.steps,action,reason});
    if(expected!==epoch)return;
    data.revision=result.revision;data.review_state=result.state;modified=false;
    if(action!=="save"){data.readonly=action!=="reopen";data.can_reopen=action==="approve";editing=action==="reopen";}
    $("annotation-preview-info").textContent=`${data.variant} · ${data.source_label||"原始编号待确认"} · ${data.review_state} · 版本 ${data.revision}`;
    message(action==="reopen"?"已撤回审核并创建新草稿版本。请重新保存并审核。":action==="save"?"草稿已保存到正式审核队列，尚未放行。":action==="approve"?"本条审核通过并冻结。全部条目处理后，点击“审核完成，继续后续处理”。":"已标记不合格并保留数据。");
    draw();await refresh();
  }
  async function exportViewer(format){
    if(!data)return;const expected=epoch;
    const params={task,job:data.id,format,variant:data.variant||"draft"};
    const show=result=>{if(epoch!==expected)return;$("review-export-status").textContent=result.message;
      if(result.state==="completed"){$("review-export-download").href="/api/annotation/viewer-file?"+new URLSearchParams({...params,token});$("review-export-download").hidden=false;}
      if(result.state==="running")exportTimer=setTimeout(async()=>{try{show(await api("/api/annotation/viewer-status?"+new URLSearchParams(params)));}catch(e){message(e.message);}},1200);};
    try{$("review-export-download").hidden=true;show(await api("/api/annotation/viewer-export",params));}catch(e){message(e.message);}
  }
  function init(){
    new ResizeObserver(layout).observe($("review-timeline-scroll"));
    for(const format of ["rrd","mcap"])$("review-export-"+format).onclick=()=>exportViewer(format);
    $("review-filter").onchange=draw;
    $("review-play").onclick=play;$("review-scrubber").oninput=()=>{pause();seek(Number($("review-scrubber").value),true);};
    $("review-back").onclick=()=>{pause();seek(frame()-1,true);};$("review-forward").onclick=()=>{pause();seek(frame()+1,true);};
    $("review-prev").onclick=()=>{if(data?.steps.length){selectedStage=Math.max(0,selectedStage-1);seek(data.steps[selectedStage].start_frame);}};
    $("review-next").onclick=()=>{if(data?.steps.length){selectedStage=Math.min(data.steps.length-1,selectedStage+1);seek(data.steps[selectedStage].start_frame);}};
    $("review-speed").onchange=()=>active.forEach(v=>v.playbackRate=Number($("review-speed").value));
    $("annotation-camera").onchange=()=>document.querySelectorAll("#review-videos figure").forEach(f=>f.classList.toggle("focused",f.dataset.camera===$("annotation-camera").value.replace("observation.images.","")));
    $("review-edit-start").onclick=()=>{editing=true;draw();};$("review-edit-stop").onclick=()=>{editing=false;draw();};
    $("review-undo").onclick=()=>{if(history.length){data.steps=history.pop();modified=true;draw();}};
    $("review-initialize").onclick=()=>{if(!data||data.readonly)return;remember();data.steps=categories.map(([phase,_,text],i)=>({stage_index:i,phase,text,start_frame:Math.floor(i*data.frames/7),end_frame:Math.floor((i+1)*data.frames/7)}));editing=true;draw();message("这是均分的人工模板，不是模型识别。必须逐一调整六个边界，并确认真实七阶段齐全。");};
    for(const [id,action] of [["review-save-local","save"],["review-approve","approve"],["review-reject","reject"],["review-reopen","reopen"]])$(id).onclick=async()=>{try{$(id).disabled=true;await submit(action);}catch(e){message(e.message);}finally{controls();}};
    $("review-export-json").onclick=()=>{if(!data)return;const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download=`${data.source_label?data.source_label.replaceAll(" / ","_"):"annotations"}_${data.variant}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
    $("review-import").onclick=()=>$("review-import-file").click();$("review-import-file").onchange=async e=>{try{const file=e.target.files[0];if(!file||!data||data.readonly)return;const v=JSON.parse(await file.text());if(v.recording_id!==data.recording_id||v.frames!==data.frames||v.fps!==data.fps||v.episode_index!==data.episode_index||v.variant!==data.variant)throw Error("文件来源或数据版本不匹配");const before=structuredClone(data.steps);remember();data.steps=v.steps;try{valid();}catch(e){data.steps=before;throw e;}editing=true;draw();message("已导入，尚未保存或审核通过。");}catch(e){message(e.message);}finally{e.target.value="";}};
    document.addEventListener("keydown",e=>{if(!data||$("view-preview").hidden||/INPUT|SELECT|TEXTAREA|BUTTON|A/.test(e.target.tagName)||e.ctrlKey||e.metaKey||e.altKey)return;if(e.code==="Space"){e.preventDefault();play();}if(["ArrowLeft","ArrowRight"].includes(e.code)){e.preventDefault();pause();seek(frame()+(e.code==="ArrowRight"?1:-1),true);}});
    document.addEventListener("visibilitychange",()=>{if(document.hidden)pause();});controls();
  }
  return {init,load,clear,pause,hasUnsaved:()=>modified};
})();
