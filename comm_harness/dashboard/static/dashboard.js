import { formatValue as fmtArgs, requestJSON } from "./http.js";
import { createInteractionController } from "./interaction.js";
import { makeAssistantTextElement } from "./markdown_table.js";
import {
  analyzePrimitiveSchema,
  readPrimitiveArguments,
  renderPrimitiveFields,
} from "./primitive_controls.js";

function $(selector) {
  return document.querySelector(selector);
}

const LANGUAGE = document.documentElement.lang === "zh-cn" ? "zh-cn" : "en";

const COPY = {
  en: {
    pageTitle: "Comm Harness · RF-Native Embodied Console",
    liveMonitor: "RF-NATIVE CONSOLE",
    commTopologyTitle: "Wireless Topology & Channel Matrix",
    planner: "planner",
    model: "model",
    nextModel: "Switch Model / Probe",
    applyModel: "Apply to Next Task",
    modelSaved: "Saved; takes effect when the next task starts.",
    defaultModel: "configured default",
    runtimeStates: {
      pending: "waiting",
      starting: "starting",
      ready: "ready",
      failed: "failed",
    },
    reasoning: "Agent Decision Stream & Command Bus",
    expandTools: "expand tools",
    autoScroll: "auto-scroll",
    resizeColumns: "Drag to resize columns",
    composerLabel: "Agent command & message composer",
    operatorResetHint: "Enter /done to confirm scene restoration and allow the robot to reset, or /abort to cancel.",
    operatorExploreHint: "Enter /continue or /abort.",
    operatorVerdictHint: "Enter /continue; the planner then continues the task.",
    taskSuggestionsLabel: "Task value suggestions",
    resizeComposer: "Drag to resize composer height · double-click to reset",
    composerPlaceholder: "Dispatch a task (e.g. /task find_vase) or send live intervention to the agent…",
    composerKeys: "Enter to send · Shift+Enter for newline · Esc to interrupt",
    commandPlaceholder: (usage) => usage,
    commandKeys: (usage) => `Enter to submit · ${usage}`,
    sessionStarting: "Starting shared robot & channel services…",
    commandReady: (usage) => `Ready for ${usage}.`,
    taskStarting: "Starting the selected TaskRun…",
    taskSwitchPending: (target) => `Task switch pending${target ? `: ${target}` : ""}.`,
    sessionFatal: "The Dashboard Session is unavailable.",
    interactionStarting: "Waiting for robot startup…",
    interactionReady: "Agent ready for commands or live intervention.",
    interactionBusy: "Agent is executing; new messages will be queued.",
    interactionUnavailable: "Agent is not accepting messages yet.",
    interruptRequested: "Interrupt requested; active tool will stop at next safe boundary.",
    interruptSucceeded: "Agent interrupted; queued messages are being submitted.",
    submittingMessage: "Submitting message…",
    pendingHeading: "Messages queued for next tool boundary",
    inactiveMessagesHeading: "Messages not submitted",
    messageStates: {
      pending: "pending",
      sending: "sending",
      failed: "failed",
      unsent: "unsent",
    },
    withdraw: "Withdraw",
    withdrawMessage: (text) => `Withdraw queued message: ${text}`,
    submitFailed: (error) => `Message was not submitted: ${error}`,
    withdrawFailed: (error) => `Message was not withdrawn: ${error}`,
    interruptFailed: (error) => `Interrupt request failed: ${error}`,
    interactionError: (error) => `Agent interaction error: ${error}`,
    unknownRequestError: "request failed",
    waitingFrame: "waiting for sensor frame…",
    frameUnavailable: (label) => `${label} unavailable`,
    resizeFrame: "Drag to resize sensor view height",
    primitiveControls: "Low-Level Primitives & RF Teleop",
    primitiveWaiting: "waiting for TaskRun",
    primitiveReady: (count) => `${count} primitives ready`,
    primitiveUnavailable: "controls unavailable",
    primitiveUnsupported: (reason) => `Unavailable: ${reason}`,
    unsupportedSchema: "unsupported schema",
    advanced: "Optional Parameters",
    executePrimitive: "Dispatch Primitive",
    executingPrimitive: "Dispatching…",
    primitiveSucceeded: "Dispatched — inspect topology, sensor view, and receipt timeline.",
    primitiveFailed: (error) => `Execution failed: ${error}`,
    notSet: "not set",
    invalidField: (field) => `${field} must be a valid value.`,
    actionTimeline: "Physical & RF Receipt Timeline",
    noActions: "No action receipts yet.",
    stateLabels: {
      starting: "STARTING",
      ready: "STANDBY",
      running: "EXECUTING",
      finished: "AGENT_DONE",
      succeeded: "AGENT_DONE",
      failed: "FAULT",
      cancelled: "INTERRUPTED",
      stale: "OFFLINE",
    },
    full: "FULL",
    episodeVideo: "episode video",
    completeRunVideo: "complete run video",
    finished: "done",
    noTranscript: "No agent reasoning or RF events yet.",
    you: "OPERATOR · COMMAND_HUB",
    initialTaskSubmitted:
      "Initial task instructions were generated from the run configuration and dispatched to the agent.",
    loading: "Loading…",
    live: "● RF LIVE",
    reconnecting: "○ reconnecting…",
    fieldRequired: (field) => `${field} is required.`,
    awaitingTask: (usage) => `Awaiting ${usage}`,
    actionReplayTitle: "Click to replay this action",
    episodeReplayTitle: "Click to replay the full episode",
    thinking: (count) => `thinking · ${count.toLocaleString()} chars`,
    toolCalls: "tool / RF calls",
    eventCount: (count) => `${count} events`,
    actionCaption: (step, action) => `receipt #${step} ${action}`,
    frameCaption: (label, index) => `${label} · step #${index}`,
    usage: (usage) =>
      `in ${usage.in.toLocaleString()} · out ${usage.out.toLocaleString()} · ${usage.tool_calls} tools`,
  },
  "zh-cn": {
    pageTitle: "Comm Harness · 通信原生具身控制台",
    liveMonitor: "RF-NATIVE CONSOLE",
    commTopologyTitle: "无线通信拓扑与信道矩阵",
    planner: "planner",
    model: "model",
    nextModel: "切换模型 / 探针",
    applyModel: "应用到下一任务",
    modelSaved: "已保存，将在下一任务启动时生效。",
    defaultModel: "默认配置",
    runtimeStates: {
      pending: "等待中",
      starting: "启动中",
      ready: "就绪",
      failed: "异常",
    },
    reasoning: "智能体决策流与指令交互",
    expandTools: "展开工具调用",
    autoScroll: "自动滚动",
    resizeColumns: "拖动调整左右分栏宽度",
    composerLabel: "智能体指令与干预输入舱",
    operatorResetHint: "输入 /done 确认场景已恢复并允许机器人复位，或输入 /abort 中止。",
    operatorExploreHint: "输入 /continue 或 /abort。",
    operatorVerdictHint: "输入 /continue，随后规划器继续执行任务。",
    taskSuggestionsLabel: "任务参数候选",
    resizeComposer: "拖动调整输入舱高度 · 双击复位",
    composerPlaceholder: "向智能体下达任务（如 /task find_vase）或发送实时干预指令…",
    composerKeys: "Enter 发送指令 · Shift+Enter 换行 · Esc 中断当前运行",
    commandPlaceholder: (usage) => usage,
    commandKeys: (usage) => `Enter 提交指令 · ${usage}`,
    sessionStarting: "正在启动共享仿真与无线信道服务…",
    commandReady: (usage) => `控制台就绪，可下达 ${usage}。`,
    taskStarting: "正在启动已选任务周期…",
    taskSwitchPending: (target) => `任务切换等待中${target ? `：${target}` : ""}。`,
    sessionFatal: "控制台会话不可用。",
    interactionStarting: "正在等待机器人节点就绪…",
    interactionReady: "节点待命，可随时下达新指令或干预消息。",
    interactionBusy: "智能体正在执行周期；新消息将进入下发队列。",
    interactionUnavailable: "节点暂未接收消息。",
    interruptRequested: "已发送中断信令；活动原语将在下一安全边界停止。",
    interruptSucceeded: "智能体已中断；正在下发队列中的指令。",
    submittingMessage: "正在下发指令…",
    pendingHeading: "等待下一工具边界下发的消息",
    inactiveMessagesHeading: "未下发的消息",
    messageStates: {
      pending: "排队中",
      sending: "下发中",
      failed: "失败",
      unsent: "未发送",
    },
    withdraw: "撤回",
    withdrawMessage: (text) => `撤回排队消息：${text}`,
    submitFailed: (error) => `指令未提交：${error}`,
    withdrawFailed: (error) => `指令未撤回：${error}`,
    interruptFailed: (error) => `中断请求失败：${error}`,
    interactionError: (error) => `智能体交互异常：${error}`,
    unknownRequestError: "请求失败",
    waitingFrame: "等待传感器画面…",
    frameUnavailable: (label) => `${label} 画面不可用`,
    resizeFrame: "拖动调整传感器视图高度",
    primitiveControls: "底层原语与通信直控 (Primitives)",
    primitiveWaiting: "等待节点就绪",
    primitiveReady: (count) => `${count} 个原语就绪`,
    primitiveUnavailable: "原语不可用",
    primitiveUnsupported: (reason) => `不可用：${reason}`,
    unsupportedSchema: "schema 不支持",
    advanced: "可选参数",
    executePrimitive: "发送原语",
    executingPrimitive: "执行中…",
    primitiveSucceeded: "原语已执行，请查看通信拓扑、传感器画面与回执时间线。",
    primitiveFailed: (error) => `执行失败：${error}`,
    notSet: "未设置",
    invalidField: (field) => `${field} 的值无效。`,
    actionTimeline: "动作与通信回执时间线 (Receipt Timeline)",
    noActions: "暂无已执行的动作回执。",
    stateLabels: {
      starting: "启动中 · STARTING",
      ready: "待命 · STANDBY",
      running: "运行中 · EXECUTING",
      finished: "周期结项 · AGENT_DONE",
      succeeded: "周期结项 · AGENT_DONE",
      failed: "运行异常 · FAULT",
      cancelled: "已中断 · INTERRUPTED",
      stale: "离线 · OFFLINE",
    },
    full: "全程",
    episodeVideo: "周期完整回放",
    completeRunVideo: "整段运行视频",
    finished: "结项",
    noTranscript: "暂无智能体推理与通信事件。",
    you: "指控中心 · COMMAND_HUB",
    initialTaskSubmitted: "已根据当前任务配置，通过指控链路向智能体下发初始任务指令。",
    loading: "正在同步节点状态…",
    live: "● 链路在线",
    reconnecting: "○ 链路重连中…",
    fieldRequired: (field) => `请填写 ${field}。`,
    awaitingTask: (usage) => `等待指令 ${usage}`,
    actionReplayTitle: "点击回放该动作切片",
    episodeReplayTitle: "点击回放完整周期视频",
    thinking: (count) => `内部推理 · ${count.toLocaleString()} 字符`,
    toolCalls: "次原语/信道调用",
    eventCount: (count) => `${count} 条事件`,
    actionCaption: (step, action) => `回执 #${step} ${action}`,
    frameCaption: (label, index) => `${label} · 步 #${index}`,
    usage: (usage) =>
      `输入 ${usage.in.toLocaleString()} · 输出 ${usage.out.toLocaleString()} · ${usage.tool_calls} 次调用`,
  },
};

const copy = COPY[LANGUAGE];
const RUNTIME_STATES = ["pending", "starting", "ready", "failed"];
let runtimeComponents = [];
let taskCommandUsage = "";
let activeNodeFilter = "all";

function applyStaticCopy() {
  for (const element of document.querySelectorAll("[data-i18n]")) {
    if (copy[element.dataset.i18n] != null) {
      element.textContent = copy[element.dataset.i18n];
    }
  }
  for (const element of document.querySelectorAll("[data-i18n-placeholder]")) {
    if (copy[element.dataset.i18nPlaceholder] != null) {
      element.placeholder = copy[element.dataset.i18nPlaceholder];
    }
  }
  for (const element of document.querySelectorAll("[data-i18n-title]")) {
    if (copy[element.dataset.i18nTitle] != null) {
      element.title = copy[element.dataset.i18nTitle];
    }
  }
  for (const element of document.querySelectorAll("[data-i18n-aria-label]")) {
    if (copy[element.dataset.i18nAriaLabel] != null) {
      element.setAttribute("aria-label", copy[element.dataset.i18nAriaLabel]);
    }
  }
}

function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

// --- Node & RF Stream Filtering ---
function applyTranscriptFilter() {
  const box = $("#transcript");
  if (!box) return;
  for (const el of box.children) {
    if (el.classList.contains("empty")) continue;
    if (activeNodeFilter === "all") {
      el.hidden = false;
      continue;
    }
    if (activeNodeFilter === "rf") {
      const isRf = el.dataset.isRf === "true" || el.querySelector?.('[data-is-rf="true"]') != null;
      el.hidden = !isRf;
      continue;
    }
    const node = el.dataset.nodeId || "";
    const hasChildNode = el.querySelector?.(`[data-node-id="${activeNodeFilter}"]`) != null;
    el.hidden = !(node === activeNodeFilter || node === "command_hub" || hasChildNode);
  }
}

function setNodeFilter(filterValue) {
  activeNodeFilter = filterValue || "all";
  const bar = $("#nodeFilterBar");
  if (bar) {
    for (const btn of bar.querySelectorAll(".filter-chip")) {
      btn.classList.toggle("active", btn.dataset.filter === activeNodeFilter);
    }
  }
  applyTranscriptFilter();
}

$("#nodeFilterBar")?.addEventListener("click", (event) => {
  const btn = event.target.closest(".filter-chip");
  if (!btn) return;
  setNodeFilter(btn.dataset.filter);
});

// --- Wireless Communication Topology Renderer ---
function renderCommTopology(topo) {
  if (!topo || typeof topo !== "object") return;
  const metrics = topo.metrics || {};
  const tx = Number(metrics.tx_packets) || 0;
  const rx = Number(metrics.delivered_packets) || 0;
  const drop = Number(metrics.dropped_packets) || 0;
  const airtime = Number(metrics.airtime_bytes) || 0;

  if ($("#rfMediumBadge") && topo.medium) {
    $("#rfMediumBadge").textContent = topo.medium;
  }
  if ($("#rfTxCount")) $("#rfTxCount").textContent = tx.toLocaleString();
  if ($("#rfRxCount")) $("#rfRxCount").textContent = rx.toLocaleString();
  if ($("#rfBytesCount")) $("#rfBytesCount").textContent = formatBytes(airtime);
  if ($("#rfDropCount")) $("#rfDropCount").textContent = drop.toLocaleString();
  if ($("#rfHeaderMeta")) {
    $("#rfHeaderMeta").textContent = `RF ${tx} pkts · ${formatBytes(airtime)}`;
  }

  // Render recent RF packets list
  const packetList = $("#rfPacketList");
  if (packetList) {
    const packets = Array.isArray(topo.recent_packets) ? topo.recent_packets : [];
    if (!packets.length) {
      packetList.innerHTML = `<div class="empty-mini">等待无线通信帧…</div>`;
    } else {
      const items = packets.slice(-8).reverse().map((pkt) => {
        const div = document.createElement("div");
        div.className = `rf-pkt-item${pkt.status === "dropped" ? " dropped" : ""}`;
        const targets = Array.isArray(pkt.recipients) ? pkt.recipients.join(",") : (pkt.recipients || "mesh");
        div.innerHTML = `
          <div class="rf-pkt-top">
            <span class="rf-pkt-route">${pkt.sender} ➔ ${targets}</span>
            <span class="rf-pkt-bytes">${pkt.bytes || 0} B · #${pkt.step ?? 0}</span>
          </div>
          <div class="rf-pkt-sub">[${pkt.kind || "FRAME"}] ${pkt.summary || ""}</div>
        `;
        return div;
      });
      packetList.replaceChildren(...items);
    }
  }

  // Render interactive SVG topology graph
  const svg = $("#topologySvg");
  if (!svg) return;
  const nodes = Array.isArray(topo.nodes) ? topo.nodes : [];
  const links = Array.isArray(topo.links) ? topo.links : [];

  // Calculate fixed tactical layout coordinates in 560x175 viewBox
  const coords = {};
  const hubNode = nodes.find((n) => n.id === "command_hub") || {
    id: "command_hub",
    label: "COMMAND_HUB",
    status: "online",
    phase: "CONTROL",
    tx_packets: 0,
    rx_packets: 0,
  };
  coords["command_hub"] = { x: 108, y: 88, node: hubNode };

  const mobileNodes = nodes.filter((n) => n.id !== "command_hub");
  mobileNodes.forEach((n, idx) => {
    const count = Math.max(1, mobileNodes.length);
    const ySpan = 112;
    const y = count === 1 ? 88 : 34 + (idx * ySpan) / (count - 1);
    const x = idx % 2 === 0 ? 415 : 445;
    coords[n.id] = { x, y, node: n };
  });

  const svgParts = [
    `<defs>
      <filter id="rfGlow" x="-25%" y="-25%" width="150%" height="150%">
        <feGaussianBlur stdDeviation="3" result="blur" />
        <feComposite in="SourceGraphic" in2="blur" operator="over" />
      </filter>
    </defs>`,
  ];

  // Draw wireless links
  for (const link of links) {
    const src = coords[link.source];
    const dst = coords[link.target];
    if (!src || !dst) continue;
    const active = (Number(link.packets) || 0) > 0;
    const stroke = active ? "#22d3ee" : "#2a4773";
    const dash = active ? "5,4" : "3,5";
    const midX = (src.x + dst.x) / 2;
    const midY = (src.y + dst.y) / 2;
    const rssiText = link.rssi_dbm != null ? `${link.rssi_dbm}dBm` : "";
    const pktsText = `${link.packets || 0}p`;

    svgParts.push(`
      <g class="topo-link">
        <line x1="${src.x}" y1="${src.y}" x2="${dst.x}" y2="${dst.y}"
          stroke="${stroke}" stroke-width="${active ? 2 : 1.2}" stroke-dasharray="${dash}" opacity="${active ? 0.9 : 0.55}" />
        ${
          active
            ? `<circle r="3.2" fill="#22d3ee" filter="url(#rfGlow)">
                <animateMotion dur="1.8s" repeatCount="indefinite" path="M${src.x},${src.y} L${dst.x},${dst.y}" />
              </circle>`
            : ""
        }
        <rect x="${midX - 42}" y="${midY - 10}" width="84" height="17" rx="4"
          fill="#091324" stroke="${active ? "#06b6d4" : "#1e3252"}" stroke-width="1" opacity="0.92" />
        <text x="${midX}" y="${midY + 2}" text-anchor="middle" fill="${active ? "#67e8f9" : "#8b9bb4"}"
          font-size="9.5" font-family="ui-monospace, monospace">${rssiText} · ${pktsText}</text>
      </g>
    `);
  }

  // Draw nodes
  for (const [id, item] of Object.entries(coords)) {
    const n = item.node;
    const isHub = id === "command_hub";
    const isActive = n.status === "active" || n.status === "online";
    const isSelected = activeNodeFilter === id;
    const ringColor = isSelected
      ? "#22d3ee"
      : isHub
        ? "#06b6d4"
        : isActive
          ? "#10b981"
          : "#475569";
    const fill = isHub ? "#0c2538" : isActive ? "#0b2926" : "#111c30";
    const phaseText = n.phase || (isHub ? "CONTROL" : "STANDBY");
    const stepInfo = !isHub && n.step ? ` · S#${n.step}` : "";

    svgParts.push(`
      <g class="topo-node" data-node="${id}" style="cursor:pointer" transform="translate(${item.x}, ${item.y})">
        <circle r="24" fill="${fill}" stroke="${ringColor}" stroke-width="${isSelected ? 2.6 : 1.8}" />
        ${
          isActive
            ? `<circle r="28" fill="none" stroke="${ringColor}" stroke-width="1" stroke-dasharray="3,3" opacity="0.55" />`
            : ""
        }
        <text x="0" y="-2" text-anchor="middle" fill="#f8fafc" font-size="10" font-weight="700"
          font-family="ui-monospace, monospace">${isHub ? "HUB" : n.label}</text>
        <text x="0" y="10" text-anchor="middle" fill="${ringColor}" font-size="8.5"
          font-family="ui-monospace, monospace">${phaseText}${stepInfo}</text>
        <text x="0" y="37" text-anchor="middle" fill="#94a3b8" font-size="9.5"
          font-family="ui-monospace, monospace">TX:${n.tx_packets || 0} RX:${n.rx_packets || 0}</text>
      </g>
    `);
  }

  svg.innerHTML = svgParts.join("");
  for (const g of svg.querySelectorAll(".topo-node")) {
    g.addEventListener("click", () => {
      const nodeId = g.dataset.node;
      if (!nodeId || nodeId === "command_hub") {
        setNodeFilter("all");
      } else {
        setNodeFilter(activeNodeFilter === nodeId ? "all" : nodeId);
      }
    });
  }
}

function defaultFrameKind() {
  return Object.keys(mediaState.frameAvailable)[0] || null;
}

function renderFrameTabs() {
  const container = $(".frame-tabs");
  const buttons = Object.keys(mediaState.frameAvailable).map(name => {
    const button = document.createElement("button");
    button.dataset.kind = name;
    button.textContent = name;
    button.classList.toggle("active", name === mediaState.kind);
    button.addEventListener("click", () => setFrameKind(name));
    return button;
  });
  container.replaceChildren(...buttons);
}

function configureDashboardSpec(spec) {
  runtimeComponents = spec.runtime_components;
  taskCommandUsage = spec.task.usage;
}

function updateFrameAvailability(available) {
  const previousNames = Object.keys(mediaState.frameAvailable);
  const names = Object.keys(available);
  mediaState.frameAvailable = available;
  if (!isRealtimeKind(mediaState.lastRealtimeKind)) {
    mediaState.lastRealtimeKind = defaultFrameKind();
  }
  if (mediaState.kind !== "video" && mediaState.kind !== "actionVideo"
      && !isRealtimeKind(mediaState.kind)) {
    mediaState.kind = mediaState.lastRealtimeKind;
    mediaState.frameIndex = -1;
    if (mediaState.kind === null) {
      resetMediaBuffers();
      $("#frameCap").textContent = copy.waitingFrame;
    }
  }
  if (names.length !== previousNames.length
      || names.some((name, index) => name !== previousNames[index])) {
    renderFrameTabs();
  }
}

function renderPlannerConfig(config) {
  const planner = config.planner || copy.notSet;
  const model = config.model || copy.defaultModel;
  const element = $("#plannerMeta");
  element.textContent = planner === "flash"
    ? "Flash Mode"
    : `${copy.planner}: ${planner} · ${copy.model}: ${model}`;
  element.title = element.textContent;
  $("#modelSettings").hidden = planner === "flash";
  $("#nextModelInput").value = config.model || "";
}

$("#modelForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const feedback = $("#modelFeedback");
  try {
    const response = await fetch("/api/session/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: $("#nextModelInput").value }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    renderPlannerConfig(result);
    feedback.textContent = copy.modelSaved;
  } catch (error) {
    feedback.textContent = error.message;
  }
});

$("#llmCheckBtn")?.addEventListener("click", async () => {
  const feedback = $("#modelFeedback");
  const modelValue = $("#nextModelInput")?.value?.trim() || "";
  feedback.textContent = "正在探测大模型连通性…";
  try {
    const result = await requestJSON("/api/llm/check", {
      method: "POST",
      body: { model: modelValue || undefined },
    });
    if (result.ok) {
      feedback.textContent = `✓ 连通正常 (${result.latency_ms}ms) · ${result.response_preview || "OK"}`;
    } else {
      feedback.textContent = `✗ 探针失败：${result.error || "不可达"}`;
    }
  } catch (error) {
    feedback.textContent = `✗ 探针异常：${error.message}`;
  }
});

// --- Quick Command Chips ---
$("#quickCmdBar")?.addEventListener("click", async (event) => {
  const chip = event.target.closest(".quick-chip");
  if (!chip) return;
  if (chip.dataset.cmd) {
    const input = $("#chatInput");
    if (input && !input.disabled) {
      input.value = chip.dataset.cmd;
      input.focus();
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    }
    return;
  }
  if (chip.dataset.primitive) {
    const primName = chip.dataset.primitive;
    const args = primName === "broadcast"
      ? { message: "HEARTBEAT_SYNC: requesting peer pose & map delta" }
      : {};
    try {
      await requestJSON("/api/session/primitive", {
        method: "POST",
        body: { name: primName, arguments: args },
      });
      refreshMeta();
    } catch (error) {
      setPrimitiveStatus(copy.primitiveFailed(error.message), "error", 5000);
    }
    return;
  }
  if (chip.dataset.action === "interrupt") {
    try {
      await requestJSON("/api/session/interrupt", { method: "POST", body: {} });
      refreshMeta();
    } catch {}
  }
});

const runState = {
  eventSource: null,
  lastStepCount: -1,
  lastEventCount: -1,
  taskGeneration: null,
};

function selectFrameTab(kind = null) {
  document.querySelectorAll(".frame-tabs button").forEach(button =>
    button.classList.toggle("active", button.dataset.kind === kind)
  );
}

const transcriptState = {
  shown: 0,
  toolGroup: null,
  inFlight: false,
  refreshAgain: false,
  initialized: false,
  epoch: 0,
};

const timelineState = {
  initialized: false,
  loaded: 0,
  episodeElement: null,
};

const mediaState = {
  kind: null,
  frameIndex: -1,
  frameAvailable: {},
  actionVideo: null,
  episodeVideoAvailable: false,
  lastRealtimeKind: null,
  returnTimer: null,
  activeImage: null,
  activeVideo: null,
  swapQueue: [],
  swapInFlight: false,
  generation: 0,
};

const primitiveState = {
  available: false,
  loadedGeneration: null,
  loadingGeneration: null,
  primitives: [],
  selected: null,
  executing: false,
  epoch: 0,
  retryAt: 0,
  statusTimer: null,
};

function setPrimitiveStatus(message = "", kind = "", timeout = 0) {
  if (primitiveState.statusTimer) clearTimeout(primitiveState.statusTimer);
  primitiveState.statusTimer = null;
  const status = $("#primitiveStatus");
  status.textContent = message;
  status.className = `primitive-status${kind ? ` ${kind}` : ""}`;
  if (timeout) {
    primitiveState.statusTimer = setTimeout(() => {
      status.textContent = "";
      status.className = "primitive-status";
      primitiveState.statusTimer = null;
    }, timeout);
  }
}

function resetPrimitivePanel() {
  primitiveState.epoch++;
  primitiveState.available = false;
  primitiveState.loadedGeneration = null;
  primitiveState.loadingGeneration = null;
  primitiveState.primitives = [];
  primitiveState.selected = null;
  primitiveState.executing = false;
  primitiveState.retryAt = 0;
  $("#primitiveButtons").replaceChildren();
  $("#primitiveRequiredFields").replaceChildren();
  $("#primitiveOptionalFields").replaceChildren();
  $("#primitiveAdvanced").hidden = true;
  $("#primitiveAvailability").textContent = copy.primitiveWaiting;
  $("#primitiveAvailability").classList.remove("available");
  $("#executePrimitive").disabled = true;
  $("#executePrimitive").textContent = copy.executePrimitive;
  setPrimitiveStatus();
}

function selectPrimitive(name) {
  if (primitiveState.executing) return;
  const primitive = primitiveState.primitives.find(item => item.name === name);
  if (!primitive?.analysis.supported) return;
  primitiveState.selected = primitive;
  for (const button of $("#primitiveButtons").querySelectorAll("button")) {
    button.setAttribute("aria-selected", String(button.dataset.name === name));
  }
  const required = primitive.analysis.fields.filter(field => field.required);
  const optional = primitive.analysis.fields.filter(field => !field.required);
  renderPrimitiveFields($("#primitiveRequiredFields"), required, copy);
  renderPrimitiveFields($("#primitiveOptionalFields"), optional, copy);
  $("#primitiveAdvanced").hidden = optional.length === 0;
  $("#primitiveAdvanced").open = false;
  $("#executePrimitive").disabled = false;
  setPrimitiveStatus();
}

function renderPrimitiveChoices(primitives) {
  primitiveState.primitives = primitives.map(primitive => ({
    ...primitive,
    analysis: analyzePrimitiveSchema(primitive),
  }));
  const buttons = primitiveState.primitives.map(primitive => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.name = primitive.name;
    button.textContent = primitive.analysis.supported
      ? primitive.name
      : `${primitive.name} · ${copy.unsupportedSchema}`;
    button.disabled = !primitive.analysis.supported;
    button.setAttribute("aria-selected", "false");
    if (primitive.analysis.supported) {
      button.addEventListener("click", () => selectPrimitive(primitive.name));
    } else {
      button.title = copy.primitiveUnsupported(primitive.analysis.reason);
      button.setAttribute("aria-label", `${primitive.name}. ${button.title}`);
    }
    return button;
  });
  $("#primitiveButtons").replaceChildren(...buttons);
  const supported = primitiveState.primitives.filter(item => item.analysis.supported);
  $("#primitiveAvailability").textContent = copy.primitiveReady(supported.length);
  $("#primitiveAvailability").classList.add("available");
  if (supported.length) {
    selectPrimitive(supported[0].name);
  }
}

async function loadPrimitiveSchemas(generation) {
  if (primitiveState.loadingGeneration === generation) return;
  primitiveState.loadingGeneration = generation;
  const epoch = ++primitiveState.epoch;
  try {
    const payload = await requestJSON("/api/session/primitives");
    if (epoch !== primitiveState.epoch || generation !== runState.taskGeneration) return;
    primitiveState.loadedGeneration = generation;
    primitiveState.loadingGeneration = null;
    primitiveState.retryAt = 0;
    renderPrimitiveChoices(Array.isArray(payload.primitives) ? payload.primitives : []);
  } catch (error) {
    if (epoch !== primitiveState.epoch) return;
    primitiveState.loadingGeneration = null;
    primitiveState.retryAt = Date.now() + 2000;
    $("#primitiveAvailability").textContent = copy.primitiveUnavailable;
    $("#primitiveAvailability").classList.remove("available");
    setPrimitiveStatus(error.message, "error", 6000);
  }
}

function syncPrimitiveAvailability(snapshot) {
  const available = Boolean(snapshot.primitives_available);
  if (!available) {
    if (primitiveState.available || primitiveState.loadingGeneration != null) {
      resetPrimitivePanel();
    }
    return;
  }
  primitiveState.available = true;
  if (primitiveState.loadedGeneration !== runState.taskGeneration &&
      Date.now() >= primitiveState.retryAt) {
    loadPrimitiveSchemas(runState.taskGeneration);
  }
}

function setPrimitiveFormDisabled(disabled) {
  primitiveState.executing = disabled;
  for (const control of $("#primitiveForm").querySelectorAll("input, select, button")) {
    control.disabled = disabled;
  }
  for (const button of $("#primitiveButtons").querySelectorAll("button")) {
    const item = primitiveState.primitives.find(primitive => primitive.name === button.dataset.name);
    button.disabled = disabled || !item?.analysis.supported;
  }
}

async function executeSelectedPrimitive(event) {
  event.preventDefault();
  const primitive = primitiveState.selected;
  if (!primitive || primitiveState.executing) return;
  let argumentsObject;
  try {
    argumentsObject = readPrimitiveArguments(
      $("#primitiveForm"),
      primitive.analysis.fields,
      copy,
    );
  } catch (error) {
    setPrimitiveStatus(error.message, "error", 6000);
    return;
  }
  const epoch = primitiveState.epoch;
  setPrimitiveFormDisabled(true);
  $("#executePrimitive").textContent = copy.executingPrimitive;
  setPrimitiveStatus(copy.executingPrimitive);
  try {
    await requestJSON("/api/session/primitive", {
      method: "POST",
      body: {
        name: primitive.name,
        arguments: argumentsObject,
      },
    });
    if (epoch === primitiveState.epoch) {
      setPrimitiveStatus(copy.primitiveSucceeded, "success", 3500);
      refreshMeta();
    }
  } catch (error) {
    if (epoch === primitiveState.epoch) {
      setPrimitiveStatus(copy.primitiveFailed(error.message), "error", 7000);
    }
  } finally {
    if (epoch === primitiveState.epoch) {
      setPrimitiveFormDisabled(false);
      $("#executePrimitive").textContent = copy.executePrimitive;
    }
  }
}

$("#primitiveForm").addEventListener("submit", executeSelectedPrimitive);

const ACTION_RETURN_DELAY_MS = 300;

function imgA() {
  return $("#frame-a");
}

function imgB() {
  return $("#frame-b");
}

function vidA() {
  return $("#video-a");
}

function vidB() {
  return $("#video-b");
}

function cancelActionReturn() {
  if (mediaState.returnTimer) {
    clearTimeout(mediaState.returnTimer);
    mediaState.returnTimer = null;
  }
}

function _bufferPair(kind) {
  return kind === "img" ? [imgA(), imgB()] : [vidA(), vidB()];
}

function _pickTarget(kind, url) {
  const [a, b] = _bufferPair(kind);
  if (a._loadedSrc === url) return a;
  if (b._loadedSrc === url) return b;
  const active = kind === "img" ? mediaState.activeImage : mediaState.activeVideo;
  return a === active ? b : a;
}

function _showBuffer(el) {
  for (const m of document.querySelectorAll(".frame-media.visible")) {
    if (m !== el) m.classList.remove("visible");
  }
  el.classList.add("visible");
  if (el.tagName === "IMG") mediaState.activeImage = el;
  else mediaState.activeVideo = el;
  for (const v of [vidA(), vidB()]) {
    if (v !== el && !v.paused) { try { v.pause(); } catch {} }
  }
}

function swapMedia(spec) {
  if (spec.source === "user") {
    for (let i = mediaState.swapQueue.length - 1; i >= 0; i--) {
      if (mediaState.swapQueue[i].source !== "user") mediaState.swapQueue.splice(i, 1);
    }
    if (mediaState.swapInFlight) {
      mediaState.generation++;
      mediaState.swapInFlight = false;
    }
  }
  mediaState.swapQueue.push(spec);
  _pumpSwap();
}

function _pumpSwap() {
  if (mediaState.swapInFlight || !mediaState.swapQueue.length) return;
  const spec = mediaState.swapQueue.shift();
  mediaState.swapInFlight = true;
  const gen = ++mediaState.generation;
  _runSwap(spec, gen, () => {
    if (gen !== mediaState.generation) return;
    mediaState.swapInFlight = false;
    _pumpSwap();
  });
}

function _runSwap(
  { kind, url, cap, errorCap, onReady },
  gen,
  done,
) {
  const target = _pickTarget(kind, url);
  let finished = false;
  let fallbackTimer = null;

  const finish = (ok) => {
    if (finished) return;
    finished = true;
    if (fallbackTimer) { clearTimeout(fallbackTimer); fallbackTimer = null; }
    if (gen !== mediaState.generation) return;
    if (!ok && kind === "img") {
      target.removeAttribute("src");
      for (const media of document.querySelectorAll(".frame-media.visible")) {
        media.classList.remove("visible");
      }
      mediaState.activeImage = null;
      if (errorCap != null) $("#frameCap").textContent = errorCap;
      done();
      return;
    }
    target._loadedSrc = ok ? url : null;
    _showBuffer(target);
    if (cap != null) $("#frameCap").textContent = cap;
    if (onReady) onReady(target);
    done();
  };

  if (target._loadedSrc === url) {
    finish(true);
    return;
  }

  _clearMediaListeners(target);
  target._loadedSrc = null;
  if (kind === "img") {
    const onload = () => finish(true);
    const onerror = () => finish(false);
    target.addEventListener("load", onload, { once: true });
    target.addEventListener("error", onerror, { once: true });
    target._swapCleanup = () => {
      target.removeEventListener("load", onload);
      target.removeEventListener("error", onerror);
    };
    target.src = url;
  } else {
    const oncan = () => finish(true);
    const onerr = () => finish(false);
    target.addEventListener("canplay", oncan, { once: true });
    target.addEventListener("error", onerr, { once: true });
    target._swapCleanup = () => {
      target.removeEventListener("canplay", oncan);
      target.removeEventListener("error", onerr);
    };
    target.src = url;
    target.load();
  }

  fallbackTimer = setTimeout(() => finish(true), 4000);
}

function _clearMediaListeners(el) {
  if (el._swapCleanup) { el._swapCleanup(); el._swapCleanup = null; }
}

function resetMediaBuffers() {
  mediaState.swapQueue.length = 0;
  mediaState.generation++;
  mediaState.swapInFlight = false;
  for (const el of [imgA(), imgB()]) {
    el.classList.remove("visible");
    _clearMediaListeners(el);
    el.removeAttribute("src");
    el._loadedSrc = null;
  }
  for (const v of [vidA(), vidB()]) {
    v.classList.remove("visible");
    _clearMediaListeners(v);
    v.onended = null; v.muted = false;
    try { v.pause(); } catch {}
    v.removeAttribute("src");
    v.load();
    v._loadedSrc = null;
  }
  mediaState.activeImage = null;
  mediaState.activeVideo = null;
}

function resetMediaForRun() {
  cancelActionReturn();
  mediaState.kind = null;
  mediaState.frameIndex = -1;
  mediaState.frameAvailable = {};
  mediaState.actionVideo = null;
  mediaState.episodeVideoAvailable = false;
  mediaState.lastRealtimeKind = null;
  resetMediaBuffers();
}

function resetTranscriptForRun() {
  transcriptState.epoch++;
  transcriptState.shown = 0;
  transcriptState.toolGroup = null;
  transcriptState.refreshAgain = false;
  transcriptState.initialized = false;
}

function resetTimelineForRun() {
  timelineState.initialized = false;
  timelineState.loaded = 0;
  timelineState.episodeElement = null;
}

function resetRenderedTaskProjection() {
  runState.lastStepCount = -1;
  runState.lastEventCount = -1;
  resetTranscriptForRun();
  resetTimelineForRun();
  resetMediaForRun();
  resetPrimitivePanel();
  interactionController.reset();
  $("#transcript").innerHTML = `<div class="empty">${copy.noTranscript}</div>`;
  $("#timeline").innerHTML = `<div class="empty">${copy.noActions}</div>`;
  $("#evCount").textContent = "";
  $("#stepCount").textContent = "";
  $("#usageMeta").textContent = "";
  $("#taskMeta").textContent = copy.awaitingTask(taskCommandUsage);
  $("#frameCap").textContent = copy.waitingFrame;
  renderFrameTabs();
}

function syncTaskGeneration(snapshot) {
  const value = snapshot.task_generation;
  if (runState.taskGeneration == null) {
    runState.taskGeneration = value;
    return "initial";
  }
  if (value < runState.taskGeneration) return "stale";
  if (value === runState.taskGeneration) return "unchanged";
  runState.taskGeneration = value;
  resetRenderedTaskProjection();
  return "changed";
}

function renderTaskMeta(task) {
  const taskMeta = $("#taskMeta");
  if (!task) {
    taskMeta.textContent = copy.awaitingTask(taskCommandUsage);
    return;
  }
  const label = document.createElement("b");
  label.textContent = `TASK · ${task.label}`;
  taskMeta.replaceChildren(label);
}

function timelineDetail(item) {
  const result = item.result;
  if (result && typeof result === "object" && !Array.isArray(result)) {
    const entries = Object.entries(result)
      .filter(([, value]) =>
        value == null || ["string", "number", "boolean"].includes(typeof value)
      )
      .slice(0, 4);
    if (entries.length) return fmtArgs(Object.fromEntries(entries)).slice(0, 100);
  } else if (result != null) {
    return fmtArgs(result).slice(0, 100);
  }
  return fmtArgs(item.args).slice(0, 100);
}

const interactionController = createInteractionController({
  copy,
  select: $,
});

function isRealtimeKind(kind) {
  return Object.hasOwn(mediaState.frameAvailable, kind);
}

function setBadge(state, error = null) {
  const b = $("#statusBadge");
  b.className = "badge b-" + (state || "stale");
  b.textContent = state ? (copy.stateLabels[state] || state) : "—";
  b.title = error || "Harness 自身执行生命周期状态（隔离外部评测器真值）";
}

function renderRuntimeStatus(runtime) {
  const container = $("#runtimeStatus");
  if (!container) return;
  if (!runtime || typeof runtime !== "object") {
    container.hidden = true;
    container.replaceChildren();
    return;
  }

  const items = runtimeComponents.map(function (component) {
    const info = runtime[component.name];
    const candidate = typeof info === "string" ? info : info?.status;
    const status = RUNTIME_STATES.includes(candidate) ? candidate : "pending";
    const item = document.createElement("span");
    item.className = `runtime-item runtime-${status}`;
    const label = component.label || component.name;
    item.textContent = `${label} · ${copy.runtimeStates[status]}`;
    if (info && typeof info === "object" && info.error) {
      item.title = info.error;
      item.setAttribute("aria-label", `${item.textContent}: ${info.error}`);
    }
    return item;
  });
  container.hidden = false;
  container.replaceChildren(...items);
}

function renderTimeline(
  tl,
  totalSteps,
  hasEpisodeVideo = mediaState.episodeVideoAvailable,
  { animateNew = false } = {},
) {
  tl = Array.isArray(tl) ? tl : [];
  mediaState.episodeVideoAvailable = !!hasEpisodeVideo;
  const el = $("#timeline");
  const shouldAnimateNew = animateNew && timelineState.initialized;
  const total = totalSteps + (mediaState.episodeVideoAvailable ? 1 : 0);
  $("#stepCount").textContent = total ? total : "";
  if (!total) {
    el.innerHTML = `<div class="empty">${copy.noActions}</div>`;
    timelineState.loaded = 0;
    timelineState.episodeElement = null;
    timelineState.initialized = true;
    return;
  }
  if (timelineState.loaded === 0 && (tl.length || mediaState.episodeVideoAvailable)) {
    el.replaceChildren();
  }
  for (const s of tl) {
    if (s.step === 0 && !s.action) continue;
    const div = document.createElement("div");
    div.className = "step";
    if (s.has_action_video) div.className += " hasclip";
    if (shouldAnimateNew) div.classList.add("entering");
    const res = s.result || {};
    const det = timelineDetail(s);
    const nodePrefix = s.node_id ? `[${s.node_id}] ` : "";
    div.innerHTML = `<span class="idx">#${s.step}</span>
      <div class="body"><span class="act">${nodePrefix}${s.action ?? "—"}</span>
      <div class="det" title="${fmtArgs(res).replace(/"/g,'&quot;')}">${det}</div></div>
      <span class="el">${s.elapsed_s != null ? s.elapsed_s + "s" : ""}</span>`;
    if (s.has_action_video) {
      div.title = copy.actionReplayTitle;
      div.addEventListener("click", () => playActionVideo(s));
    }
    el.insertBefore(div, timelineState.episodeElement);
  }
  timelineState.loaded = totalSteps;
  if (mediaState.episodeVideoAvailable && !timelineState.episodeElement) {
    const div = document.createElement("div");
    div.className = "step episode";
    if (shouldAnimateNew) div.classList.add("entering");
    div.title = copy.episodeReplayTitle;
    div.innerHTML = `<span class="idx">${copy.full}</span>
      <div class="body"><span class="act">${copy.episodeVideo}</span>
      <div class="det">${copy.completeRunVideo}</div></div>
      <span class="el">${copy.finished}</span>`;
    div.addEventListener("click", playEpisodeVideo);
    el.appendChild(div);
    timelineState.episodeElement = div;
  }
  timelineState.initialized = true;
}

function makeNodeBadge(nodeId) {
  if (!nodeId) return "";
  const isHub = nodeId === "command_hub";
  return `<span class="ev-node-badge${isHub ? " node-hub" : ""}">${nodeId}</span>`;
}

function makeToolEl(ev) {
  const div = document.createElement("div");
  div.className = "ev " + ev.type;
  const toolName = ev.tool || ev.name || "tool";
  const nodeId = ev.node_id || "robot_0";
  div.dataset.nodeId = nodeId;
  if (toolName === "broadcast" || toolName.startsWith("comm_")) {
    div.dataset.isRf = "true";
  }
  if (ev.type === "tool_call") {
    div.innerHTML = `${makeNodeBadge(nodeId)}→ <span class="tname">${toolName}</span> <span class="args"></span>`;
    div.querySelector(".args").textContent = fmtArgs(ev.args);
  } else {
    const rawResult = ev.result !== undefined ? ev.result : ev.content;
    const isErr = rawResult && typeof rawResult === "object" && rawResult.is_error;
    div.innerHTML = `${makeNodeBadge(nodeId)}← <span class="tname ${isErr ? "err" : ""}">${toolName}</span> <span class="args"></span>`;
    div.querySelector(".args").textContent = fmtArgs(rawResult);
  }
  return div;
}

function makeThinkingEl(ev) {
  const div = document.createElement("div");
  div.className = "ev thinking";
  div.dataset.nodeId = ev.node_id || "robot_0";
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  const text = ev.text || "";
  summary.textContent = copy.thinking(text.length);
  const pre = document.createElement("pre");
  pre.textContent = text;
  details.appendChild(summary);
  details.appendChild(pre);
  div.appendChild(details);
  return div;
}

function appendEvents(events, animateNew = false) {
  const box = $("#transcript");
  if (transcriptState.shown === 0) { box.innerHTML = ""; transcriptState.toolGroup = null; }
  for (const ev of events) {
    if (ev.type === "tool_call" || ev.type === "tool_result") {
      if (!transcriptState.toolGroup) {
        const g = document.createElement("div");
        g.className = "toolgroup";
        g.dataset.toolCalls = "0";
        g.dataset.nodeId = ev.node_id || "robot_0";
        if (animateNew) g.classList.add("entering");
        g.innerHTML = `<div class="tg-head"><span class="tg-count">0</span> ${copy.toolCalls}</div><div class="tg-body"></div>`;
        g.querySelector(".tg-head").addEventListener("click", () => g.classList.toggle("open"));
        box.appendChild(g);
        transcriptState.toolGroup = g;
      }
      const toolEl = makeToolEl(ev);
      if (toolEl.dataset.isRf === "true") {
        transcriptState.toolGroup.dataset.isRf = "true";
      }
      transcriptState.toolGroup.querySelector(".tg-body").appendChild(toolEl);
      if (ev.type === "tool_call") {
        const n = Number(transcriptState.toolGroup.dataset.toolCalls) + 1;
        transcriptState.toolGroup.dataset.toolCalls = String(n);
        transcriptState.toolGroup.querySelector(".tg-count").textContent = n;
      }
    } else {
      transcriptState.toolGroup = null;
      if (ev.type === "thinking") {
        const thinking = makeThinkingEl(ev);
        if (animateNew) thinking.classList.add("entering");
        box.appendChild(thinking);
      } else if (ev.type === "text" || ev.type === "assistant") {
        const text = makeAssistantTextElement(ev.text || "");
        text.dataset.nodeId = ev.node_id || "robot_0";
        if (ev.node_id) {
          const badge = document.createElement("span");
          badge.className = "ev-node-badge";
          badge.textContent = ev.node_id;
          text.prepend(badge);
        }
        if (animateNew) text.classList.add("entering");
        box.appendChild(text);
      } else {
        const div = document.createElement("div");
        div.className = "ev " + ev.type;
        div.dataset.nodeId = ev.node_id || (ev.type === "user" ? "command_hub" : "robot_0");
        if (animateNew) div.classList.add("entering");
        if (ev.type === "initial_prompt") {
          div.classList.add("meta");
          div.textContent = copy.initialTaskSubmitted;
        } else if (ev.type === "meta") {
          const tagStr = ev.tag || "META";
          if (tagStr.includes("RF") || tagStr.includes("无线") || tagStr.includes("原语")) {
            div.classList.add("rf-event");
            div.dataset.isRf = "true";
          }
          div.textContent = `[${tagStr}] ${ev.text}`;
        } else {
          div.textContent = ev.text;
          if (ev.type === "user") {
            div.dataset.roleLabel = copy.you;
            div.dataset.isRf = "true";
          }
        }
        box.appendChild(div);
      }
    }
  }
  transcriptState.shown += events.length;
  $("#evCount").textContent = transcriptState.shown
    ? copy.eventCount(transcriptState.shown)
    : "";
  applyTranscriptFilter();
  if ($("#autoscroll").checked) box.scrollTop = box.scrollHeight;
}

async function refreshTranscript() {
  if (transcriptState.inFlight) { transcriptState.refreshAgain = true; return; }
  transcriptState.inFlight = true;
  const taskGeneration = runState.taskGeneration;
  const epoch = transcriptState.epoch;
  try {
    const r = await requestJSON(
      `/api/session/transcript?since=${transcriptState.shown}`
    );
    if (
      taskGeneration !== runState.taskGeneration
      || epoch !== transcriptState.epoch
    ) {
      transcriptState.refreshAgain = true;
      return;
    }
    const animateNew = transcriptState.initialized;
    if (r.events && r.events.length) appendEvents(r.events, animateNew);
    else if (transcriptState.shown === 0) {
      $("#transcript").innerHTML = `<div class="empty">${copy.noTranscript}</div>`;
    }
    transcriptState.initialized = true;
  } catch (e) {
    /* transient — next tick retries */
  } finally {
    transcriptState.inFlight = false;
    if (transcriptState.refreshAgain) { transcriptState.refreshAgain = false; refreshTranscript(); }
  }
}

function setFrameKind(kind) {
  cancelActionReturn();
  if (isRealtimeKind(kind)) {
    mediaState.lastRealtimeKind = kind;
  }
  mediaState.kind = kind;
  if (kind !== "actionVideo") mediaState.actionVideo = null;
  selectFrameTab(kind);
  mediaState.frameIndex = -1;
  refreshFrame(undefined, { source: "user" });
}

function finishActionPlayback(actionVideo) {
  if (mediaState.actionVideo !== actionVideo || mediaState.returnTimer) return;
  mediaState.returnTimer = setTimeout(() => {
    mediaState.returnTimer = null;
    if (mediaState.actionVideo !== actionVideo) return;
    const returnKind = mediaState.lastRealtimeKind || defaultFrameKind();
    mediaState.actionVideo = null;
    mediaState.kind = returnKind;
    mediaState.frameIndex = -1;
    selectFrameTab(returnKind);
    refreshMeta();
  }, ACTION_RETURN_DELAY_MS);
}

function playActionVideo(step) {
  if (!step || !step.has_action_video) return;
  cancelActionReturn();
  mediaState.actionVideo = step;
  mediaState.kind = "actionVideo";
  selectFrameTab();
  mediaState.frameIndex = -1;
  refreshFrame(undefined, { source: "user" });
}

function playEpisodeVideo() {
  if (!mediaState.episodeVideoAvailable) return;
  cancelActionReturn();
  mediaState.actionVideo = null;
  mediaState.kind = "video";
  selectFrameTab();
  mediaState.frameIndex = -1;
  refreshFrame(undefined, { source: "user" });
}

function refreshFrame(idx, opts = {}) {
  if (mediaState.kind === null) return;
  const source = opts.source || "auto";

  if (mediaState.kind === "actionVideo") {
    if (!mediaState.actionVideo) return;
    const actionVideo = mediaState.actionVideo;
    const url = `/api/session/action-video?step=${encodeURIComponent(mediaState.actionVideo.step)}`;
    const cap = copy.actionCaption(
      mediaState.actionVideo.step,
      mediaState.actionVideo.action,
    );
    swapMedia({
      kind: "video",
      url,
      cap,
      source,
      onReady: (v) => {
        try { v.currentTime = 0; } catch {}
        v.playbackRate = 0.5;
        v.muted = false;
        v.onended = () => finishActionPlayback(actionVideo);
        const p = v.play();
        if (p && typeof p.catch === "function") {
          p.catch(() => finishActionPlayback(actionVideo));
        }
      },
    });
    return;
  }

  if (mediaState.kind === "video") {
    const url = "/api/session/video";
    swapMedia({
      kind: "video",
      url,
      cap: copy.episodeVideo,
      source,
      onReady: (v) => {
        v.playbackRate = 1.0;
        v.muted = false;
        v.onended = null;
      },
    });
    return;
  }

  if (idx != null && idx === mediaState.frameIndex) return;
  mediaState.frameIndex = idx ?? mediaState.frameIndex;
  const url = `/api/session/frame?kind=${encodeURIComponent(mediaState.kind)}&t=${Date.now()}`;
  swapMedia({
    kind: "img",
    url,
    cap: copy.frameCaption(
      mediaState.kind,
      mediaState.frameIndex,
    ),
    errorCap: copy.frameUnavailable(mediaState.kind),
    source,
  });
}

function applySessionSnapshot(snapshot) {
  const generationState = syncTaskGeneration(snapshot);
  if (generationState === "stale") return;
  setBadge(snapshot.state, snapshot.control_error || snapshot.error);
  renderRuntimeStatus(snapshot.runtime);
  syncPrimitiveAvailability(snapshot);
  interactionController.applySnapshot(snapshot);
  updateFrameAvailability(snapshot.frame_available || {});
  if (snapshot.comm_topology) {
    renderCommTopology(snapshot.comm_topology);
  }
  if (snapshot.usage) $("#usageMeta").textContent = copy.usage(snapshot.usage);
  refreshTranscriptIfChanged(snapshot);
  return generationState;
}

async function refreshMeta() {
  const timelineSince = timelineState.loaded;
  const r = await requestJSON(
    `/api/session/state?timeline_since=${timelineSince}`,
  );
  const generationState = applySessionSnapshot(r);
  if (generationState == null) return;
  const currentTask = r.current_task;
  renderTaskMeta(currentTask);
  const timelineCurrent = timelineSince === timelineState.loaded;
  if (timelineCurrent) {
    renderTimeline(r.timeline || [], r.n_steps, r.has_video, {
      animateNew: timelineState.initialized,
    });
    runState.lastStepCount = r.n_steps;
  }
  if (!r.has_video && mediaState.kind === "video") setFrameKind(defaultFrameKind());
  if (
    isRealtimeKind(mediaState.kind)
    && currentTask
  ) refreshFrame(r.frame_idx);
}

function refreshTranscriptIfChanged(snapshot) {
  if (snapshot.n_events == null || snapshot.n_events === runState.lastEventCount) return;
  runState.lastEventCount = snapshot.n_events;
  refreshTranscript();
}

function connectSSE() {
  if (runState.eventSource) runState.eventSource.close();
  runState.eventSource = new EventSource("/api/session/stream");
  runState.eventSource.onmessage = (e) => {
    const sig = JSON.parse(e.data);
    const generationState = applySessionSnapshot(sig);
    if (generationState == null) return;
    $("#connMeta").textContent = copy.live;
    if (generationState === "changed") {
      refreshMeta();
      return;
    }
    if (sig.n_steps !== runState.lastStepCount) {
      runState.lastStepCount = sig.n_steps;
      refreshMeta();
      return;
    }
    if (sig.has_video && !mediaState.episodeVideoAvailable) refreshMeta();
    if (
      isRealtimeKind(mediaState.kind)
      && sig.frame_idx != null
      && sig.frame_idx !== mediaState.frameIndex
    ) refreshFrame(sig.frame_idx);
  };
  runState.eventSource.onerror = () => {
    $("#connMeta").textContent = copy.reconnecting;
  };
}

function connectSession() {
  runState.taskGeneration = null;
  resetRenderedTaskProjection();
  renderRuntimeStatus(null);
  $("#transcript").innerHTML = `<div class="empty">${copy.loading}</div>`;
  $("#timeline").innerHTML = '<div class="empty">…</div>';
  connectSSE();
}

$("#showtools").addEventListener("change", (e) => {
  $("#transcript").classList.toggle("alltools", e.target.checked);
});

// --- draggable splitters ---
function setupSplitter(handle, opts) {
  if (!handle || !opts.container) return;
  const saved = localStorage.getItem(opts.store);
  if (saved) opts.container.style.setProperty(opts.prop, saved);
  handle.addEventListener("mousedown", (e) => {
    e.preventDefault();
    handle.classList.add("dragging");
    document.body.classList.add("resizing");
    document.body.style.cursor = opts.axis === "x" ? "col-resize" : "row-resize";
    const rect = opts.container.getBoundingClientRect();
    const offsetTop = opts.offsetEl ? opts.offsetEl.getBoundingClientRect().height : 0;
    const move = (ev) => {
      let v;
      if (opts.axis === "x") {
        v = opts.fromEnd ? rect.right - ev.clientX : ev.clientX - rect.left;
      } else {
        v = opts.fromEnd ? rect.bottom - ev.clientY : ev.clientY - rect.top - offsetTop;
      }
      const limit = (opts.axis === "x" ? rect.width : rect.height) - offsetTop;
      v = Math.max(opts.min, Math.min(v, limit - opts.max));
      const px = v + "px";
      opts.container.style.setProperty(opts.prop, px);
      localStorage.setItem(opts.store, px);
    };
    const up = () => {
      handle.classList.remove("dragging");
      document.body.classList.remove("resizing");
      document.body.style.cursor = "";
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  });
  handle.addEventListener("dblclick", () => {
    opts.container.style.removeProperty(opts.prop);
    localStorage.removeItem(opts.store);
  });
}

setupSplitter($("#gutterV"), {
  axis: "x", container: $("main"), prop: "--leftw",
  min: 320, max: 340, store: "wm.leftw",
});
setupSplitter($("#gutterH"), {
  axis: "y", container: $(".col.right"), offsetEl: $("#topologyPanel"), prop: "--frameh",
  min: 140, max: 160, store: "wm.frameh",
});
setupSplitter($("#composerGrip"), {
  axis: "y", fromEnd: true, container: $(".col.left"), prop: "--composerh",
  min: 146, max: 160, store: "wm.composerh",
});

async function boot() {
  applyStaticCopy();
  try {
    const [dashboardSpec, sessionConfig] = await Promise.all([
      requestJSON("/api/commands"),
      requestJSON("/api/session/config"),
    ]);
    configureDashboardSpec(dashboardSpec);
    renderPlannerConfig(sessionConfig);
    interactionController.configureTaskCommand(dashboardSpec.task);
    connectSession();
    refreshMeta();
  } catch (error) {
    console.error("Failed to load Dashboard configuration", error);
  }
}

boot();
