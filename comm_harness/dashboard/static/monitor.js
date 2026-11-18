'use strict';
let monitorTab = 'services', monitorData = null, comparisonData = null;
const comparedRuns = new Set();
const phaseNames = {BOOTSTRAP:'初始化',OBSERVE:'观测',PLAN:'规划',GROUND:'目标定位',VALIDATE:'校验',EXECUTE:'执行',VERIFY:'验证',FINALIZE:'汇总',TERMINAL:'结束',IDLE:'等待'};
const statusNames = {starting:'加载中',ready:'初始化完成',configured:'已配置',running:'运行中',failed:'失败',ok:'已返回',error:'异常',pending:'请求中',incomplete:'未收到结束记录',live:'心跳正常',stale:'心跳超时',ended:'已结束',historical:'历史节点',unrecorded:'未记录心跳',unusable:'无可用决策',parsed:'已解析'};
const timeText = ts => ts ? new Date(ts * 1000).toLocaleTimeString('zh-CN') : '未记录';
const seconds = n => n === null || n === undefined ? '未记录' : Number(n).toFixed(2)+' s';
function table(headers, rows) {
  const wrap=node('div','table-scroll'), t=node('table','monitor-table'), head=node('tr');
  headers.forEach(h=>head.append(node('th','',h))); const thead=node('thead'); thead.append(head); t.append(thead);
  const body=node('tbody');
  rows.forEach(values=>{const row=node('tr');values.forEach(v=>{const cell=node('td');cell.append(v instanceof Node?v:node('span','',fmt(v)));row.append(cell);});body.append(row);});
  t.append(body);wrap.append(t);return wrap;
}
function recordDetails(value, label='查看记录') {
  const d=node('details','record-details');d.dataset.key=label+':'+(value?.span_id || value?.decision_id || value?.event_id || value?.[0]?.span_id || '');
  d.append(node('summary','',label),node('pre','',JSON.stringify(value,null,2)));return d;
}
function note(text) {return node('p','monitor-note',text);}
function renderMonitoring(data) {
  monitorData=data.monitoring || null; const m=monitorData;
  $('monitor-source').textContent=!m?'未接入':m.source==='live_schema_v1'?'执行层事件 · 3 秒刷新':m.source==='legacy'?'历史日志 · 部分指标未记录':'暂无监控记录';
  $('phases').replaceChildren();
  const phase=m?.phase?.phase;
  for(const key of ['BOOTSTRAP','OBSERVE','PLAN','GROUND','EXECUTE','VERIFY','FINALIZE','TERMINAL']) {
    const el=node('span','phase'+(phase===key?' active':''),phaseNames[key]);$('phases').append(el);
  }
  const usage=m?.model?.usage, oldUsage=m?.model?.historical_usage;
  $('monitor-summary').replaceChildren(
    metric('最近阶段',phaseNames[phase] || '未记录'),
    metric('模型请求 / 重试',`${fmt(m?.model?.requests)} / ${fmt(m?.model?.retries)}`),
    metric('已报告 Token',fmt(usage?.total_tokens ?? oldUsage?.total_tokens)),
    metric('请求累计耗时',seconds(m?.durations?.model_request_s)));
  renderMonitorTab();
}
function renderMonitorTab() {
  const box=$('monitor-content');
  const key=d=>(d.parentElement.closest('.decision-card')?.dataset.key || '')+'/'+d.dataset.key;
  const opened=new Set([...box.querySelectorAll('details[open]')].map(key)), scroll=box.scrollTop;
  renderMonitorBody();
  box.querySelectorAll('details').forEach(d=>{d.open=opened.has(key(d));});box.scrollTop=scroll;
}
function renderMonitorBody() {
  const box=$('monitor-content'), m=monitorData;box.replaceChildren();
  if(!m){box.append(note('选择运行记录后查看监控。'));return;}
  if(m.backlog)box.append(note('正在补读事件；当前统计尚未覆盖完整记录。'));
  if(monitorTab==='services') {
    const services=new Map(m.services.map(s=>[s.service,s]));
    box.append(table(['组件','最近状态','记录时间','详情'],['worker','dataset','assets','simulator','perception','mapping','model'].map(name=>{
      const s=services.get(name);return [({worker:'执行进程',dataset:'任务与场景',assets:'模型资源',simulator:'仿真器',perception:'视觉感知',mapping:'地图',model:'上游模型'})[name],statusNames[s?.status] || '未记录',timeText(s?.ts),s?recordDetails(s):'—'];
    })));
    if(m.gpu)box.append(recordDetails(m.gpu,'启动时 GPU 快照'));
    box.append(note('这里显示本次运行的初始化记录。上游模型请求能否成功请看“模型请求”；当前连接与 GPU 可在左侧手动检测。'));
  } else if(monitorTab==='models') {
    box.append(note(`配置预算：最多 ${fmt(m.model.budget?.max_calls)} 次请求 / ${fmt(m.model.budget?.max_total_tokens)} Token。`));
    box.append(note(`请求异常：${fmt(m.model.errors)} 次；JSON 解析重试：${fmt(m.model.json_retries)} 次。`));
    const requests=[...m.model.recent_requests].reverse();
    if(requests.length)box.append(table(['时间 / 步数','模型 / 阶段','尝试','状态','耗时','Token','详情'],requests.map(r=>[
      `${timeText(r.started_at)} · ${fmt(r.step)}`,`${r.model || ''} · ${phaseNames[r.phase] || ''}`,r.attempt,
      r.empty?'空响应':statusNames[r.status] || r.status,r.status==='pending'?`${seconds(r.waiting_s)}（等待中）`:seconds(r.duration_s),
      r.usage?.total_tokens,recordDetails(r)])));
    else box.append(note('此运行没有逐次模型请求记录。旧实验若保存了用量，可在下方查看。'));
    box.append(note('耗时为客户端请求总耗时，包含服务端等待和网络；Token 仅汇总服务端已报告的用量。API 返回成功不等于决策或任务成功。'));
    if(Object.keys(m.model.historical_usage || {}).length)box.append(recordDetails(m.model.historical_usage,'历史用量汇总'));
  } else if(monitorTab==='decisions') {
    if(!m.decisions.length)box.append(note('旧实验缺少模型决策关联编号，可在下方“工具与执行事件”查看原始回执。'));
    for(const d of [...m.decisions].reverse()) {
      const item=node('details','decision-card');
      item.dataset.key=d.decision_id;
      item.append(node('summary','',`步 ${fmt(d.step)} · ${d.purpose || '决策'} · ${statusNames[d.status] || '已应用'}`));
      const requests=m.model.recent_requests.filter(r=>r.model_call_id===d.model_call_id);
      const events=m.events.filter(e=>e.decision_id===d.decision_id && !e.event.startsWith('MODEL_'));
      item.append(note('请求引用的观测 → 解析决策 → 当前采用的计划 → 工具执行与回执。关联表示计划生效期间的执行上下文。'),
        recordDetails(requests,'上游请求与观测编号'),recordDetails(d,'解析内容与采用的计划'),recordDetails(events,'相关执行事件（最近窗口）'));
      box.append(item);
    }
    box.append(note('显示模型公开返回的决策及执行记录；历史窗口之外的完整关联请读取运行目录的 monitor.jsonl。'));
  } else if(monitorTab==='nodes') {
    if(m.nodes.length)box.append(table(['节点','角色','状态','最近阶段','步数','心跳年龄'],m.nodes.map(n=>[
      n.id || n.node_id,n.role || '未记录',statusNames[n.status] || n.status,phaseNames[n.phase] || '未记录',n.step,seconds(n.heartbeat_age_s)])));
    else box.append(note('此历史运行未记录节点注册与心跳。新单机运行会注册 robot 节点；AP、基站和 USRP 接入后可使用同一事件格式。'));
    box.append(note('心跳只表明监控线程仍在工作；任务卡住与否还需结合阶段、请求等待时间和工具回执判断。'));
  } else if(monitorTab==='communication') {
    const c=m.communication;
    box.append(note(c.mode==='ideal'?'通信模式：理想传输。下方为已记录的应用层消息。':'通信模式：当前单机导航未接入节点间传输，指标未采集。'));
    if(c.statistics) {
      const s=c.statistics;
      box.append(table(['发送消息','投递副本','源字节','按收件人数累计字节','过期消息'],[[s.messages_sent,s.messages_delivered,s.source_bytes,s.link_bytes,s.expired]]));
    }
    if(c.recent_messages.length)box.append(table(['消息','发送方 → 收件方','类型','字节','时间 / 创建 tick','投递与信息年龄'],[...c.recent_messages].reverse().map(x=>[
      x.message_id,`${x.sender || '未记录'} → ${(x.recipients || []).join(', ')}`,x.kind,x.serialized_bytes,
      `${timeText(x.sent_at)} / ${fmt(x.created_tick)}`,recordDetails(x.delivery_events || [],'查看投递记录')])));
    box.append(note(c.note));
    box.append(note(`重复抑制事件：${fmt(c.duplicates_recorded)}；有序号记录的乱序投递：${fmt(c.reordered_deliveries)}。age_ticks 是投递时的消息年龄，尚未记录消息被决策实际采用的时间。`));
  }
}
function updateCompareButton() {
  $('compare').textContent=`对比所选实验（${comparedRuns.size} / 6）`;
  $('compare').disabled=comparedRuns.size<2 || comparedRuns.size>6;
}
function compareCheckbox(run) {
  const input=node('input','compare-check');input.type='checkbox';input.checked=comparedRuns.has(run.id);
  input.setAttribute('aria-label','对比 '+run.id);
  input.onchange=()=>{if(input.checked && comparedRuns.size>=6){input.checked=false;error('最多选择 6 条运行记录');return;}input.checked?comparedRuns.add(run.id):comparedRuns.delete(run.id);updateCompareButton();};
  return input;
}
function exportJson(data,name) {
  if(!data)return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})), a=node('a');
  a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function initializeMonitor() {
  document.querySelectorAll('[data-monitor-tab]').forEach(button=>button.onclick=()=>{
    monitorTab=button.dataset.monitorTab;
    document.querySelectorAll('[data-monitor-tab]').forEach(b=>b.setAttribute('aria-selected',String(b===button)));renderMonitorTab();
  });
  $('probe').onclick=async()=>{
    $('probe').disabled=true;const selected=selectionKey();
    try{const p=await api('/api/probe',selection());if(selected!==selectionKey())return;
      $('probe-result').hidden=false;$('probe-result').textContent=`${timeText(p.checked_at)}\n模型端口：${p.model_endpoint.status==='tcp_reachable'?'可连接':p.model_endpoint.status==='unreachable'?'连接失败':'未配置'}\nGPU（编号、名称、已用 MiB、总量 MiB、利用率 %）：\n${p.gpu || '未取得数据'}\n${p.note}`;
    }catch(e){error(e.message);}finally{$('probe').disabled=false;}
  };
  $('compare').onclick=async()=>{try{
    comparisonData=await api('/api/compare?ids='+encodeURIComponent([...comparedRuns].join(',')));
    const c=comparisonData,box=$('comparison');box.replaceChildren();$('comparison-panel').hidden=false;
    box.append(note(c.matched_recorded_conditions?'已记录的任务、模型、预算和种子一致。':`条件差异：${c.differing_conditions.join(', ') || '无已知差异'}；缺失条件：${c.missing_conditions.join(', ') || '无'}。`));
    box.append(table(['运行','任务成功','步数','路程 m','SPL','总耗时','调用 / Token','通信源字节'],c.rows.map(r=>[
      r.id,r.success===null?'未评测':Number(r.success)>0?'成功':'未成功',r.steps,r.path_length_m,r.spl,seconds(r.elapsed_s),`${fmt(r.model_calls)} / ${fmt(r.tokens)}`,r.communication?.source_bytes])));
    box.append(recordDetails(c.rows.map(r=>({id:r.id,...r.conditions})),'核对实验条件'),note(c.note));
  }catch(e){error(e.message);}};
  $('close-compare').onclick=()=>{$('comparison-panel').hidden=true;};
  $('export-monitor').onclick=()=>exportJson({run_id:currentId,monitoring:monitorData},`${currentId}-monitor.json`);
  $('export-compare').onclick=()=>exportJson(comparisonData,'experiment-comparison.json');
}
