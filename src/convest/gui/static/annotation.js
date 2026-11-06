"use strict";
window.annotationUI = (() => {
  let view = "workflow", preview = null, previewTask = null, signature = "";
  const labels = {pending:"等待标注",processing:"标注中",completed:"已完成",needs_review:"旧版保留结果",excluded:"已隔离并保留",history_skipped:"已有数据未纳入",retry_network:"断网 / 限流，自动重试",unknown:"请求结果未知，待恢复",blocked_credentials:"Key / 权限 / 额度阻塞",waiting_space:"等待本机空间",failed:"导出失败，保留待恢复",empty:"无成功条目",preparing_video:"检查完整视频",uploading_video:"上传视频至模型服务",waiting_model:"等待模型理解全片",recovering_response:"查询已有模型结果"};
  const title = x => labels[x] || x || "尚未开始";
  const run = async action => { try { await action(); } catch (e) { toast(e.message, true); } };
  function navigate(name) {
    if(name!=="preview")window.reviewUI.pause();
    view = name;
    for (const key of ["workflow","annotation","preview","resources"]) $("view-"+key).hidden = key !== name;
    document.querySelectorAll("[data-view]").forEach(b => {b.classList.toggle("active",b.dataset.view===name);b.setAttribute("aria-current",b.dataset.view===name?"page":"false");});
    render();
  }
  function render() {
    if (!draft || !$("annotation-task")) return;
    const task = current(), a = state().annotation || {episodes:[],batches:[]};
    const options = draft.tasks.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
    if ($("annotation-task").innerHTML !== options) $("annotation-task").innerHTML = options;
    $("annotation-task").value = selected || "";
    $("annotation-state").textContent = !a.enabled ? "未启用" : a.blocked ? "当前任务阻塞" : a.paused ? "标注已暂停" : state().state === "running" ? "运行中" : "任务尚未运行";
    $("annotation-pause").textContent = a.paused ? "继续标注" : "暂停标注";
    $("annotation-pause").disabled = !a.enabled;
    $("annotation-retry").disabled = !a.enabled;
    $("annotation-start").disabled = !task || !task.enabled || ["running","starting","stopping"].includes(state().state);
    $("annotation-notice").textContent = a.blocked?.error || (dirty ? "有未保存设置：先停止当前任务，再保存设置并开始。" : "标注关闭时，Key 和额度不会影响原有清洗、转换及上传。");
    const progress = a.progress || {};
    const completed = (a.episodes||[]).filter(j=>j.state==="completed");
    const previewable = (a.episodes||[]).filter(j=>["completed","needs_review"].includes(j.state));
    const trials = snapshot.annotation_trials || [];
    $("annotation-progress").textContent = `共 ${a.episodes?.length||0} 条 · 成功 ${completed.length} 条` + (progress.id ? ` · 最近阶段：${title(progress.phase)}` : "") + (a.paused ? " · 当前请求结束后暂停，不再发送新请求" : "");
    $("annotation-jobs").innerHTML = (a.episodes||[]).map(j=>{
      let usage={};try {usage=JSON.parse(j.usage||"{}");}catch (_) {}
      const retry=j.next_ns>Date.now()*1e6 ? `；下次尝试 ${new Date(j.next_ns/1e6).toLocaleTimeString()}` : "";
      return `<tr><td>${esc(j.route)} / ${esc(j.recording_id)}</td><td>${j.source_ep}</td><td>${esc(title(j.state))}</td><td>${j.runs}</td><td>${usage.total_tokens == null ? "—" : esc(usage.total_tokens)+" tokens"}</td><td>${esc((j.error||"")+retry)}</td></tr>`;
    }).join("") || '<tr><td colspan="6">尚无标注记录。保存并启动启用标注的任务后，自动接收转换完成的 Episode。</td></tr>';
    $("annotation-batches").innerHTML = (a.batches||[]).map(b=>`<p><strong>${esc(b.route)} / ${esc(b.batch_id)}</strong> · ${esc(title(b.state))}<br><code>${esc(b.output||b.error||"等待封存与标注完成")}</code></p>`).join("") || '<p class="hint">尚无封存后的标注成品。</p>';
    const sig=selected+":"+previewable.map(j=>j.id+":"+j.state).join(",")+":"+trials.map(t=>t.id+":"+t.status).join(",");
    if(sig!==signature) {
      const previous=$("annotation-preview-job").value;signature=sig;
      $("annotation-preview-job").innerHTML=previewable.map(j=>`<option value="${esc(j.id)}">${esc(title(j.state))} · ${esc(j.route)} · Episode ${j.source_ep} · ${esc(j.recording_id)}</option>`).join("")+
        trials.map(t=>`<option value="trial:${esc(t.id)}">单独测试草稿 · Episode ${t.episode_index} · ${esc(t.id)}</option>`).join("");
      if(previewable.some(j=>j.id===previous)||trials.some(t=>"trial:"+t.id===previous)) $("annotation-preview-job").value=previous;
      if(previewTask && previewTask!==selected) {preview=null;window.reviewUI.clear();$("annotation-preview-info").textContent="当前任务已切换，请重新选择结果。";}
    }
    const c=snapshot.credential||{};
    $("annotation-key-state").textContent=c.configured ? "已配置 · "+c.masked : "尚未配置 API Key";
    $("annotation-resources").textContent=`本机可用空间：${bytes(snapshot.disk.free)} / ${bytes(snapshot.disk.total)}。低于 8 GiB 时暂停新转换与标注；正式导出还需要足够的成品空间。202：${draft.data_server.host}:${draft.data_server.port}；训练服务器：${draft.training_server.host}:${draft.training_server.port}。`;
    $("annotation-load-preview").disabled=!(previewable.length||trials.length);
  }
  async function loadPreview() {
    const id=$("annotation-preview-job").value;if(!id)return;
    const task=selected, trial=id.startsWith("trial:"), button=$("annotation-load-preview");button.disabled=true;
    try {
      const url=trial ? "/api/annotation/trial-preview?"+new URLSearchParams({run:id.slice(6)}) :
        "/api/annotation/preview?"+new URLSearchParams({task,job:id});
      const data=await api(url);
      if((!trial&&task!==selected)||id!==$("annotation-preview-job").value)return;
      preview=data;previewTask=trial?null:task;window.reviewUI.load(data,task);
    } finally { render(); }
  }
  function init() {
    document.querySelectorAll("[data-view]").forEach(b=>b.onclick=()=>navigate(b.dataset.view));
    $("annotation-task").onchange=()=>{selected=$("annotation-task").value;renderEditor();renderTasks();renderProgress();};
    $("annotation-save").onclick=()=>$("save").click();
    $("annotation-start").onclick=()=>$("start-one").click();
    $("annotation-pause").onclick=()=>run(async()=>{snapshot=await api("/api/annotation/pause",{task:selected,paused:!state().annotation?.paused});render();});
    $("annotation-retry").onclick=()=>run(async()=>{snapshot=await api("/api/annotation/retry",{task:selected});render();});
    $("annotation-save-key").onclick=()=>run(async()=>{
      const field=$("annotation-key");await api("/api/annotation/key",{key:field.value});field.value="";
      await refresh();toast("本机凭据已保存，受阻任务会自动检测恢复。");
    });
    $("annotation-check-key").onclick=()=>run(async()=>{
      const b=$("annotation-check-key");b.disabled=true;$("annotation-connection-result").textContent="正在检查…";
      try {const result=await api("/api/annotation/check",{});$("annotation-connection-result").textContent=result.message;}finally{b.disabled=false;}
    });
    $("annotation-load-preview").onclick=()=>run(loadPreview);
    window.reviewUI.init();
    $("annotation-connections").onclick=()=>navigate("workflow");
    render();
  }
  return {init,render};
})();
