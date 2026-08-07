"use strict";

const page = document.body;
const endpoints = {
  mock: page.dataset.mockEndpoint,
  mockTrigger: page.dataset.mockTriggerEndpoint,
  messages: page.dataset.messagesEndpoint,
  detections: page.dataset.detectionsEndpoint,
  reports: page.dataset.projectReportsEndpoint,
  summaries: page.dataset.dailyReportsEndpoint,
};

const messageList = document.querySelector("#message-list");
const chatSelect = document.querySelector("#chatid-select");
const chatTitle = document.querySelector("#chat-title");
const messageCount = document.querySelector("#message-count");
const scrollOldestButton = document.querySelector("#scroll-oldest");
const scrollLatestButton = document.querySelector("#scroll-latest");
const groupList = document.querySelector("#group-list");
const composer = document.querySelector("#composer");
const messageInput = document.querySelector("#message-input");
const senderUserid = document.querySelector("#sender-userid");
const senderName = document.querySelector("#sender-name");
const sendButton = document.querySelector("#send-button");
const reportDate = document.querySelector("#report-date");
const previewButton = document.querySelector("#preview-button");
const summaryResult = document.querySelector("#summary-result");
const debugRecords = document.querySelector("#debug-records");
const dialog = document.querySelector("#json-dialog");
const dialogTitle = document.querySelector("#dialog-title");
const dialogContent = document.querySelector("#dialog-content");
const toast = document.querySelector("#toast");

const senderNames = new Map([
  ["builder-zhang", "张工"],
  ["builder-li", "李工"],
  ["manager-wang", "王经理"],
]);
const groupNames = new Map([
  ["construction-group-001", "施工日报测试群 001"],
  ["construction-group-002", "施工日报测试群 002"],
  ["construction-group-003", "施工日报测试群 003"],
]);
const detectionLabels = {
  report_candidate: "日报候选",
  needs_review: "待大模型复核",
  ignored: "普通聊天",
  not_applicable: "不适用",
};
const extractionLabels = {
  pending: "等待提取",
  completed: "提取完成",
  needs_review: "信息不完整，需确认",
  failed: "结构化提取失败",
};
const generationLabels = {
  completed: "汇总完成",
  needs_review: "汇总需要确认",
};
const publicationLabels = {
  draft: "待人工确认",
  confirmed: "已人工确认",
  sending: "处理中",
  sent: "已发送",
  send_failed: "处理失败",
};
const sendStatusLabels = {
  sending: "发送中",
  sent: "发送成功",
  send_failed: "发送失败",
};
const transportLabels = {
  mock: "本地 Mock",
  real: "真实传输",
};
const fieldLabels = {
  project_name: "项目名称",
  report_date: "日报日期",
  weather: "天气情况",
  management_count: "管理人员数量",
  worker_count: "施工人员数量",
  equipment: "机械设备",
  work_items: "施工内容",
  tomorrow_plan: "明日计划",
  safety_status: "安全情况",
  quality_status: "质量情况",
  missing_fields: "缺失信息",
  extraction_status: "提取状态",
};
const sensitiveKeyPattern = /(?:api[-_]?key|token|encodingaeskey|response[-_]?url|secret)/i;
const debugHistory = [];
let currentChatid = "construction-group-001";
let currentPreview = null;
let toastTimer = null;

class ApiError extends Error {
  constructor(status, body, operation) {
    super(toChineseError(status, body, operation));
    this.status = status;
    this.body = body;
  }
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function button(text, className, action) {
  const element = node("button", className, text);
  element.type = "button";
  element.addEventListener("click", action);
  return element;
}

function display(value, empty = "未提供") {
  if (value === null || value === undefined || value === "") return empty;
  return String(value);
}

function fieldListText(fields) {
  if (!Array.isArray(fields) || !fields.length) return "无";
  return fields.map(field => fieldLabels[field] || "其他信息").join("、");
}

function humanizeFieldText(value) {
  let result = String(value ?? "");
  for (const [field, label] of Object.entries(fieldLabels)) {
    result = result.replaceAll(field, label);
  }
  return result;
}

function summaryProjectNames(data) {
  const names = new Map();
  for (const collectionName of ["source_reports", "missing_data", "review_reports", "projects"]) {
    const collection = Array.isArray(data?.[collectionName]) ? data[collectionName] : [];
    for (const item of collection) {
      if (item?.msgid && item?.project_name) names.set(item.msgid, item.project_name);
    }
  }
  return [...names.entries()].sort((left, right) => right[0].length - left[0].length);
}

function humanizeSummaryText(value, data) {
  let result = humanizeFieldText(value);
  for (const [msgid, projectName] of summaryProjectNames(data)) {
    result = result.replaceAll(msgid, `项目“${projectName}”`);
  }
  return result.replace(/dev-chat-[a-zA-Z0-9-]+/g, "项目名称未识别的日报");
}

function localToday() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function timeLabel(value) {
  if (!value) return "刚刚";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function sanitized(value) {
  if (Array.isArray(value)) return value.map(sanitized);
  if (value && typeof value === "object") {
    const result = {};
    for (const [key, item] of Object.entries(value)) {
      result[key] = sensitiveKeyPattern.test(key) ? "[已隐藏]" : sanitized(item);
    }
    return result;
  }
  if (typeof value === "string") return redactSensitiveText(value);
  return value;
}

function redactSensitiveText(value) {
  return value
    .replace(/(?:https?|mock):\/\/[^\s"']+/gi, "[链接已隐藏]")
    .replace(
      /((?:api[-_ ]?key|token|encodingaeskey|secret)\s*[:=]\s*)[^\s,;]+/gi,
      "$1[已隐藏]",
    );
}

function prettyJson(value) {
  return JSON.stringify(sanitized(value), null, 2);
}

function recordDebug(endpoint, status, requestBody, responseBody) {
  debugHistory.unshift({ endpoint, status, requestBody, responseBody });
  debugHistory.splice(10);
  debugRecords.replaceChildren();
  for (const entry of debugHistory) {
    const card = node("article", "debug-record");
    const header = node("header");
    header.append(node("code", "", entry.endpoint), node("strong", "", `HTTP ${entry.status}`));
    card.append(header);
    card.append(node("p", "debug-label", "请求 JSON"));
    card.append(node("pre", "", entry.requestBody === undefined ? "（无）" : prettyJson(entry.requestBody)));
    card.append(node("p", "debug-label", "响应 JSON"));
    card.append(node("pre", "", prettyJson(entry.responseBody)));
    debugRecords.append(card);
  }
}

async function apiRequest(endpoint, options = {}, operation = "请求") {
  let response;
  let responseBody;
  try {
    response = await fetch(endpoint, {
      ...options,
      headers: options.body
        ? { "Content-Type": "application/json", ...(options.headers || {}) }
        : options.headers,
      credentials: "same-origin",
    });
    const text = await response.text();
    if (text) {
      try {
        responseBody = JSON.parse(text);
      } catch (_error) {
        responseBody = { message: text };
      }
    } else {
      responseBody = {};
    }
    recordDebug(endpoint, response.status, options.debugBody, responseBody);
  } catch (_error) {
    recordDebug(endpoint, "网络错误", options.debugBody, { detail: "本地服务连接失败" });
    throw new ApiError(0, {}, operation);
  }
  if (!response.ok) throw new ApiError(response.status, responseBody, operation);
  return responseBody;
}

function toChineseError(status, body, operation) {
  const backendDetail = body && typeof body.detail === "string"
    ? redactSensitiveText(body.detail)
    : "";
  if (operation === "结构化提取") {
    if (status === 503) return "结构化提取失败：未配置大模型，请先在服务端配置 LLM_API_KEY、LLM_MODEL 和 LLM_BASE_URL。";
    if (status === 504) return `结构化提取失败：${backendDetail || "大模型调用超时"}。可稍后手动重试。`;
    if (status === 502) return `结构化提取失败：大模型返回了无效结果。${backendDetail}`;
    if (status === 409) return "这条消息不是日报候选或需要人工确认的文本消息，不能执行结构化提取。";
  }
  if (status === 409 && operation === "模拟发送汇总日报") {
    return `模拟发送未执行：${backendDetail || "请确认快照已人工确认，且当前没有正在进行或已经完成的发送。"}`;
  }
  if (status === 409 && operation === "创建本地模拟触发消息") {
    return `无法创建模拟触发消息：${backendDetail || "所选群聊与汇总快照不一致。"}`;
  }
  if (status === 0) return "无法连接本地服务，请确认应用仍在运行。";
  if (status === 404) return `${operation}失败：目标数据不存在或当前环境未启用此功能。`;
  if (status === 409) return `${operation}未完成：当前数据状态不允许执行该操作。${backendDetail}`;
  if (status === 422) return `${operation}失败：提交内容不符合接口要求。${backendDetail}`;
  if (status === 503) return `${operation}暂不可用：服务端依赖尚未配置。${backendDetail}`;
  if (status >= 500) return `${operation}失败：本地服务处理异常。${backendDetail}`;
  return `${operation}失败。${backendDetail}`;
}

function showToast(message, isError = false) {
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.className = isError ? "toast show error" : "toast show";
  toastTimer = window.setTimeout(() => { toast.className = "toast"; }, 3200);
}

function showJson(title, value) {
  dialogTitle.textContent = title;
  dialogContent.textContent = prettyJson(value);
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

function statusPill(status, labels) {
  let tone = "muted";
  if (["report_candidate", "completed", "confirmed", "sent"].includes(status)) tone = "success";
  if (["needs_review", "pending"].includes(status)) tone = "warning";
  if (["failed", "send_failed"].includes(status)) tone = "danger";
  return node("span", `pill ${tone}`, labels[status] || display(status));
}

function scrollMessages(position) {
  messageList.scrollTo({
    top: position === "oldest" ? 0 : messageList.scrollHeight,
    behavior: "smooth",
  });
  messageList.focus({ preventScroll: true });
}

function renderSystemMessage(text, tone = "normal") {
  const row = node("article", `message-row system ${tone}`);
  row.append(node("div", "avatar", "机器人"));
  const body = node("div", "message-body");
  const meta = node("div", "message-meta");
  meta.append(node("span", "", "日报机器人"), node("span", "", "刚刚"));
  body.append(meta, node("div", "bubble", text));
  row.append(body);
  messageList.append(row);
  messageList.scrollTop = messageList.scrollHeight;
}

function renderDetection(detection, msgid) {
  const card = node("section", "detection-card");
  const top = node("div", "detection-top");
  top.append(
    statusPill(detection.detection_status, detectionLabels),
    node("span", "detection-score", `识别分数 ${display(detection.score, "—")}`),
  );
  card.append(top);
  const rules = node("div", "rule-list");
  const matchedRules = Array.isArray(detection.matched_rules) ? detection.matched_rules : [];
  if (matchedRules.length) {
    for (const rule of matchedRules) rules.append(node("span", "pill", rule));
  } else {
    rules.append(node("span", "pill muted", "未命中规则"));
  }
  card.append(rules, node("p", "detection-reason", `识别原因：${display(detection.reason)}`));

  if (["report_candidate", "needs_review"].includes(detection.detection_status)) {
    const actions = node("div", "action-row");
    actions.append(
      button("提取结构化字段", "ghost-button", () => extractReport(msgid)),
      button("查看消息详情", "ghost-button", () => viewMessageDetail(msgid)),
    );
    card.append(actions);
  }
  return card;
}

function addDataItem(grid, label, value, wide = false) {
  const wrapper = node("div", wide ? "wide-data" : "");
  wrapper.append(node("dt", "", label), node("dd", "", display(value)));
  grid.append(wrapper);
}

function equipmentText(items) {
  if (!Array.isArray(items) || !items.length) return "未提供";
  return items.map(item => `${display(item.name)} ${display(item.count)} ${display(item.unit)}`).join("；");
}

function workItemsText(items) {
  if (!Array.isArray(items) || !items.length) return "未提供";
  return items.map(item => {
    const prefix = item.location ? `${item.location}：` : "";
    const progress = item.progress ? `（${item.progress}）` : "";
    return `${prefix}${display(item.content)}${progress}`;
  }).join("；");
}

function renderExtraction(report) {
  const card = node("section", "extraction-card");
  const heading = node("div", "detection-top");
  heading.append(node("h3", "", "结构化日报字段"), statusPill(report.extraction_status, extractionLabels));
  card.append(heading);
  const grid = node("dl", "data-grid");
  addDataItem(grid, "项目名称", report.project_name);
  addDataItem(grid, "日报日期", report.report_date);
  addDataItem(grid, "天气情况", report.weather);
  addDataItem(grid, "管理人员数量", report.management_count);
  addDataItem(grid, "施工人员数量", report.worker_count);
  addDataItem(grid, "提取状态", extractionLabels[report.extraction_status] || "未知");
  addDataItem(grid, "机械设备", equipmentText(report.equipment), true);
  addDataItem(grid, "施工内容", workItemsText(report.work_items), true);
  addDataItem(
    grid,
    "缺失信息",
    fieldListText(report.missing_fields),
    true,
  );
  if (report.error_message) addDataItem(grid, "错误说明", report.error_message, true);
  card.append(grid);
  return card;
}

function renderMessage(message, detection, report) {
  const row = node("article", "message-row user");
  row.dataset.msgid = message.msgid;
  const name = senderNames.get(message.sender_userid) || message.sender_userid || "测试用户";
  row.append(node("div", "avatar", name.slice(0, 2)));
  const body = node("div", "message-body");
  const meta = node("div", "message-meta");
  meta.append(node("span", "", name), node("span", "", timeLabel(message.received_at)));
  body.append(meta, node("div", "bubble", display(message.text_content, "（非文本消息）")));
  if (detection) body.append(renderDetection(detection, message.msgid));
  if (report) body.append(renderExtraction(report));
  row.append(body);
  return row;
}

async function loadChat() {
  messageCount.textContent = "正在加载…";
  messageList.replaceChildren();
  const loading = node("div", "empty-state");
  loading.append(node("span", "", "载"), node("p", "", "正在加载当前群聊消息…"));
  messageList.append(loading);
  const query = `chatid=${encodeURIComponent(currentChatid)}&limit=500`;
  try {
    const [messages, detections, reports] = await Promise.all([
      apiRequest(`${endpoints.messages}?${query}`, {}, "加载消息"),
      apiRequest(`${endpoints.detections}?${query}`, {}, "加载识别结果"),
      apiRequest(`${endpoints.reports}?${query}`, {}, "加载提取结果"),
    ]);
    const detectionByMsgid = new Map((detections.items || []).map(item => [item.msgid, item]));
    const reportByMsgid = new Map((reports.items || []).map(item => [item.msgid, item]));
    const items = [...(messages.items || [])].sort((a, b) => String(a.received_at).localeCompare(String(b.received_at)));
    messageCount.textContent = items.length >= 500 ? "最近 500 条消息" : `共 ${items.length} 条消息`;
    messageList.replaceChildren();
    renderSystemMessage("欢迎使用施工日报本地验收页。这里的消息只进入本地 mock 接口，不会发送到真实交建通群聊。");
    for (const item of items) {
      messageList.append(renderMessage(item, detectionByMsgid.get(item.msgid), reportByMsgid.get(item.msgid)));
    }
    messageList.scrollTop = messageList.scrollHeight;
  } catch (error) {
    messageCount.textContent = "加载失败";
    messageList.replaceChildren();
    renderSystemMessage(error.message, "error");
  }
}

function uniqueMsgid() {
  const suffix = window.crypto && typeof window.crypto.randomUUID === "function"
    ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `dev-chat-${suffix}`;
}

async function sendMessage() {
  const content = messageInput.value.trim();
  if (!content) {
    showToast("请输入要发送的消息。", true);
    messageInput.focus();
    return;
  }
  const payload = {
    msgid: uniqueMsgid(),
    aibotid: "dev-chat-bot",
    chatid: currentChatid,
    chattype: "group",
    from: { userid: senderUserid.value, name: senderName.value },
    msgtype: "text",
    text: { content },
  };
  sendButton.disabled = true;
  try {
    const result = await apiRequest(
      endpoints.mock,
      { method: "POST", body: JSON.stringify(payload), debugBody: payload },
      "发送模拟消息",
    );
    messageInput.value = "";
    await loadChat();
    const label = detectionLabels[result.detection_status] || "已接收";
    renderSystemMessage(`消息已保存，识别结果：${label}。${display(result.reason, "")}`);
    await autoExtractNewMessage(result);
  } catch (error) {
    renderSystemMessage(error.message, "error");
    showToast(error.message, true);
  } finally {
    sendButton.disabled = false;
    messageInput.focus();
  }
}

function canExtractDetection(detectionStatus) {
  return ["report_candidate", "needs_review"].includes(detectionStatus);
}

async function requestReportExtraction(msgid) {
  return apiRequest(
    `${endpoints.messages}/${encodeURIComponent(msgid)}/extract-report`,
    { method: "POST", debugBody: {} },
    "结构化提取",
  );
}

async function autoExtractNewMessage(messageResult) {
  if (!canExtractDetection(messageResult.detection_status)) return null;
  renderSystemMessage("规则初筛完成，正在先调用大模型提取并复核结构化字段…");
  summaryResult.replaceChildren(node(
    "div",
    "alert-box",
    "消息已保存，正在调用大模型提取项目、日期、天气、人数、机械和施工内容…",
  ));
  try {
    const report = await requestReportExtraction(messageResult.msgid);
    if (report.report_date) reportDate.value = report.report_date;
    await loadChat();
    renderSystemMessage(
      report.extraction_status === "needs_review"
        ? `大模型已完成复核，但仍缺少：${fieldListText(report.missing_fields)}，需要人工处理。`
        : "大模型已完成结构化复核，右侧汇总已自动刷新。",
    );
    await previewSummary({ skipAutoExtraction: true });
    return report;
  } catch (error) {
    renderSystemMessage(`消息已经保存，但自动大模型复核未完成。${error.message}`, "error");
    summaryResult.replaceChildren(node(
      "div",
      "alert-box danger",
      `消息已保存，但尚未生成可汇总的结构化日报。${error.message} 可在消息卡片中手动重试。`,
    ));
    showToast(error.message, true);
    return null;
  }
}

async function viewMessageDetail(msgid) {
  try {
    const detail = await apiRequest(`${endpoints.messages}/${encodeURIComponent(msgid)}`, {}, "查看消息详情");
    showJson(`消息详情 · ${msgid}`, detail);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function extractReport(msgid) {
  const article = [...messageList.querySelectorAll(".message-row")].find(item => item.dataset.msgid === msgid);
  const actionButtons = article ? article.querySelectorAll("button") : [];
  actionButtons.forEach(item => { item.disabled = true; });
  try {
    const report = await requestReportExtraction(msgid);
    if (article) {
      const body = article.querySelector(".message-body");
      const oldCard = body.querySelector(".extraction-card");
      if (oldCard) oldCard.remove();
      body.append(renderExtraction(report));
    }
    renderSystemMessage(
      report.extraction_status === "needs_review"
        ? "结构化字段已提取，但存在缺失字段，需要人工确认。"
        : "结构化字段提取完成，可继续生成汇总预览。",
    );
    if (report.report_date) {
      reportDate.value = report.report_date;
      await previewSummary({ skipAutoExtraction: true });
    }
  } catch (error) {
    renderSystemMessage(error.message, "error");
    showToast(error.message, true);
  } finally {
    actionButtons.forEach(item => { item.disabled = false; });
  }
}

async function extractPendingReportsBeforePreview() {
  const query = `chatid=${encodeURIComponent(currentChatid)}&limit=500`;
  let detections;
  let reports;
  try {
    [detections, reports] = await Promise.all([
      apiRequest(`${endpoints.detections}?${query}`, {}, "查询待复核日报"),
      apiRequest(`${endpoints.reports}?${query}`, {}, "查询结构化日报"),
    ]);
  } catch (error) {
    renderSystemMessage(`汇总前检查失败：${error.message}`, "error");
    summaryResult.replaceChildren(node("div", "alert-box danger", error.message));
    return false;
  }

  const reportByMsgid = new Map((reports.items || []).map(item => [item.msgid, item]));
  const pending = (detections.items || []).filter(item => (
    canExtractDetection(item.detection_status) && (
      !reportByMsgid.has(item.msgid)
      || (
        reportByMsgid.get(item.msgid).extraction_status === "failed"
        && /大模型(?:调用超时|调用失败|网络请求失败)/.test(
          reportByMsgid.get(item.msgid).error_message || "",
        )
      )
    )
  ));
  if (!pending.length) return true;

  renderSystemMessage(`发现 ${pending.length} 条尚未结构化的日报，生成汇总前先调用大模型复核…`);
  summaryResult.replaceChildren(node(
    "div",
    "alert-box",
    `正在调用大模型处理 ${pending.length} 条待复核日报，请稍候…`,
  ));
  let completed = 0;
  let stoppedError = null;
  for (const item of pending) {
    try {
      await requestReportExtraction(item.msgid);
      completed += 1;
    } catch (error) {
      stoppedError = error;
      if ([0, 503, 504].includes(error.status)) break;
    }
  }
  if (completed) await loadChat();
  if (completed) {
    renderSystemMessage(`大模型预处理完成：${completed} 条日报已生成结构化字段。`);
  }
  if (stoppedError) {
    renderSystemMessage(`部分日报未能完成大模型复核：${stoppedError.message}`, "error");
  }
  if (stoppedError && completed === 0) {
    summaryResult.replaceChildren(node(
      "div",
      "alert-box danger",
      `消息已保存，但没有生成可汇总的结构化日报。${stoppedError.message} 可在消息卡片中手动重试。`,
    ));
    return false;
  }
  return true;
}

function addSummaryList(parent, title, items, formatter) {
  const section = node("section", "summary-section");
  section.append(node("h4", "", title));
  const list = node("ul", "plain-list");
  for (const item of items) list.append(node("li", "", formatter(item)));
  section.append(list);
  parent.append(section);
}

function renderMarkdown(markdown, summaryData) {
  const container = node("article", "markdown-card");
  let list = null;
  for (const rawLine of String(markdown || "").split(/\r?\n/)) {
    const line = humanizeSummaryText(rawLine.trim(), summaryData);
    if (!line) {
      list = null;
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      list = null;
      container.append(node(`h${heading[1].length}`, "", heading[2]));
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      if (!list) {
        list = node("ul");
        container.append(list);
      }
      list.append(node("li", "", bullet[1]));
      continue;
    }
    list = null;
    container.append(node("p", "", line));
  }
  return container;
}

function renderSummary(data) {
  currentPreview = data;
  summaryResult.replaceChildren();
  const metrics = node("div", "summary-metrics");
  const values = [
    [data.project_count, "项目数"],
    [display(data.management_total, "—"), "管理人员"],
    [display(data.worker_total, "—"), "施工人员"],
  ];
  for (const [value, label] of values) {
    const metric = node("div", "metric");
    metric.append(node("strong", "", value), node("span", "", label));
    metrics.append(metric);
  }
  summaryResult.append(metrics);

  const status = node("div", "summary-status");
  status.append(node("h3", "", `${display(data.report_date)} 汇总`), statusPill(data.generation_status, generationLabels));
  summaryResult.append(status);

  if (Array.isArray(data.equipment) && data.equipment.length) {
    addSummaryList(summaryResult, "机械设备汇总", data.equipment, item => `${item.name}：${item.count} ${item.unit}`);
  }
  if (Array.isArray(data.warnings) && data.warnings.length) {
    const warning = node("div", "alert-box");
    warning.append(node("strong", "", generationLabels.needs_review));
    for (const text of data.warnings) {
      warning.append(node("div", "", humanizeSummaryText(text, data)));
    }
    summaryResult.append(warning);
  }
  if (Array.isArray(data.duplicate_projects) && data.duplicate_projects.length) {
    const duplicates = node("div", "alert-box danger");
    duplicates.append(node("strong", "", "重复项目"));
    for (const item of data.duplicate_projects) {
      const reportCount = Array.isArray(item.reports) ? item.reports.length : 0;
      duplicates.append(
        node(
          "div",
          "",
          `${item.project_name || "未命名项目"}：发现 ${reportCount} 条重复日报，请人工选择有效记录`,
        ),
      );
    }
    summaryResult.append(duplicates);
  }
  if (Array.isArray(data.missing_data) && data.missing_data.length) {
    addSummaryList(
      summaryResult,
      "还需补充的信息",
      data.missing_data,
      item => `${item.project_name ? `项目“${item.project_name}”` : "项目名称未识别"}：${fieldListText(item.fields)}`,
    );
  }

  summaryResult.append(renderMarkdown(data.markdown_content, data));
  const actions = node("div", "summary-actions");
  actions.append(
    button("查看原始 JSON", "secondary-button", () => showJson("汇总预览原始 JSON", data)),
    button("保存汇总快照", "primary-button wide", saveSummary),
  );
  summaryResult.append(actions);
}

function renderSummaryChatMessage(data) {
  const previewKey = `${data.chatid}:${data.report_date}`;
  const previous = [...messageList.querySelectorAll(".summary-chat-message")]
    .find(item => item.dataset.previewKey === previewKey);
  if (previous) previous.remove();

  const row = node("article", "message-row system summary-chat-message");
  row.dataset.previewKey = previewKey;
  row.append(node("div", "avatar summary-avatar", "日报"));

  const body = node("div", "message-body");
  const meta = node("div", "message-meta");
  meta.append(
    node("span", "", "日报机器人"),
    node("span", "", "刚刚"),
    node("span", "preview-only-badge", "本地预览 · 未发送真实群聊"),
  );

  const bubble = node("div", "bubble summary-chat-bubble");
  const heading = node("div", "summary-chat-heading");
  const headingText = node("div");
  headingText.append(
    node("span", "eyebrow", "汇总日报"),
    node("h2", "", `${data.report_date} 施工日报汇总预览`),
  );
  heading.append(headingText, statusPill(data.generation_status, generationLabels));
  bubble.append(heading);

  const metrics = node("div", "summary-metrics summary-chat-metrics");
  for (const [value, label] of [
    [data.project_count, "项目数"],
    [display(data.management_total, "—"), "管理人员"],
    [display(data.worker_total, "—"), "施工人员"],
  ]) {
    const metric = node("div", "metric");
    metric.append(node("strong", "", value), node("span", "", label));
    metrics.append(metric);
  }
  bubble.append(metrics);

  if (Array.isArray(data.equipment) && data.equipment.length) {
    addSummaryList(
      bubble,
      "机械设备汇总",
      data.equipment,
      item => `${item.name}：${item.count} ${item.unit}`,
    );
  }
  if (Array.isArray(data.warnings) && data.warnings.length) {
    const warning = node("div", "alert-box");
    warning.append(node("strong", "", "需要关注"));
    const missingCount = Array.isArray(data.missing_data) ? data.missing_data.length : 0;
    const duplicateCount = Array.isArray(data.duplicate_projects) ? data.duplicate_projects.length : 0;
    if (missingCount) warning.append(node("div", "", `${missingCount} 个项目存在缺失信息`));
    if (duplicateCount) warning.append(node("div", "", `${duplicateCount} 个项目存在重复日报`));
    if (!missingCount && !duplicateCount) {
      warning.append(node("div", "", `${data.warnings.length} 项内容需要确认，详见下方日报`));
    }
    bubble.append(warning);
  }

  bubble.append(renderMarkdown(data.markdown_content, data));
  const actions = node("div", "summary-actions");
  actions.append(
    button("查看原始 JSON", "secondary-button", () => showJson("汇总预览原始 JSON", data)),
    button("查看右侧工作台", "secondary-button", () => {
      document.querySelector(".workflow-panel").scrollTo({ top: 0, behavior: "smooth" });
    }),
  );
  bubble.append(actions);
  body.append(meta, bubble);
  row.append(body);
  messageList.append(row);
  messageList.scrollTo({ top: messageList.scrollHeight, behavior: "smooth" });
}

async function previewSummary(options = {}) {
  if (!reportDate.value) {
    showToast("请选择日报日期。", true);
    return;
  }
  const payload = { chatid: currentChatid, report_date: reportDate.value };
  previewButton.disabled = true;
  try {
    if (!options.skipAutoExtraction) {
      const extractionReady = await extractPendingReportsBeforePreview();
      if (!extractionReady) return;
    }
    const preview = await apiRequest(
      `${endpoints.summaries}/preview`,
      { method: "POST", body: JSON.stringify(payload), debugBody: payload },
      "生成汇总预览",
    );
    renderSummary(preview);
    renderSummaryChatMessage(preview);
    if (preview.generation_status === "needs_review") {
      showToast("汇总已生成，但存在需要人工确认的内容。", true);
    } else {
      showToast("汇总预览已生成。", false);
    }
  } catch (error) {
    currentPreview = null;
    summaryResult.replaceChildren(node("div", "alert-box danger", error.message));
    showToast(error.message, true);
  } finally {
    previewButton.disabled = false;
  }
}

function codedLabel(value, labels) {
  return `${labels[value] || display(value)}（${display(value)}）`;
}

function renderSendOutcome(summary, sendResult) {
  const section = node("section", "send-outcome");
  section.append(node("strong", "", sendResult.attempt.send_status === "sent" ? "模拟发送成功" : "模拟发送未成功"));
  const grid = node("dl", "data-grid send-data-grid");
  addDataItem(grid, "汇总发布状态", codedLabel(sendResult.publication_status, publicationLabels));
  addDataItem(grid, "传输方式", codedLabel(sendResult.attempt.transport, transportLabels));
  addDataItem(grid, "发送状态", codedLabel(sendResult.attempt.send_status, sendStatusLabels));
  addDataItem(grid, "发送完成时间", summary.sent_at ? timeLabel(summary.sent_at) : "未完成");
  section.append(grid, node("p", "mock-only-note", "仅本地 Mock，未发送真实群聊。"));
  return section;
}

function renderSendAttempts(items, container) {
  container.replaceChildren();
  if (!Array.isArray(items) || !items.length) {
    container.append(node("div", "empty-attempts", "暂无发送记录。"));
    return;
  }
  items.forEach((attempt, index) => {
    const card = node("article", "send-attempt-card");
    const heading = node("div", "detection-top");
    heading.append(
      node("strong", "", `第 ${items.length - index} 次发送`),
      statusPill(attempt.send_status, sendStatusLabels),
    );
    const grid = node("dl", "data-grid send-data-grid");
    addDataItem(grid, "开始时间", timeLabel(attempt.attempted_at));
    addDataItem(grid, "完成时间", attempt.completed_at ? timeLabel(attempt.completed_at) : "尚未完成");
    addDataItem(grid, "传输方式", codedLabel(attempt.transport, transportLabels));
    addDataItem(grid, "发送状态", codedLabel(attempt.send_status, sendStatusLabels));
    addDataItem(grid, "HTTP 状态码", attempt.http_status_code ?? "无");
    addDataItem(grid, "错误类型", attempt.error_type || "无");
    addDataItem(grid, "错误说明", attempt.error_message || "无", true);
    addDataItem(grid, "响应地址", "已脱敏", true);
    card.append(heading, grid);
    container.append(card);
  });
}

async function viewSendAttempts(summary, container, target) {
  target.disabled = true;
  try {
    const result = await apiRequest(
      `${endpoints.summaries}/${summary.id}/send-attempts`,
      {},
      "查看发送记录",
    );
    renderSendAttempts(result.items, container);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    target.disabled = false;
  }
}

function renderMockSendChatMessage(summary, sendResult) {
  const previous = messageList.querySelector(`.mock-send-message[data-summary-id="${summary.id}"]`);
  if (previous) previous.remove();

  const row = node("article", "message-row system mock-send-message");
  row.dataset.summaryId = String(summary.id);
  row.append(node("div", "avatar", "发送"));
  const body = node("div", "message-body");
  const meta = node("div", "message-meta");
  meta.append(
    node("span", "", "日报机器人"),
    node("span", "", "刚刚"),
    node("span", "preview-only-badge", "仅本地 Mock · 未发送真实群聊"),
  );
  const bubble = node("div", "bubble mock-send-bubble");
  bubble.append(node("strong", "", "模拟发送成功"));
  const grid = node("dl", "data-grid send-data-grid");
  addDataItem(grid, "汇总快照", `#${summary.id}`);
  addDataItem(grid, "汇总发布状态", codedLabel(sendResult.publication_status, publicationLabels));
  addDataItem(grid, "传输方式", codedLabel(sendResult.attempt.transport, transportLabels));
  addDataItem(grid, "发送状态", codedLabel(sendResult.attempt.send_status, sendStatusLabels));
  addDataItem(grid, "发送时间", timeLabel(sendResult.sent_at), true);
  bubble.append(grid, node("p", "mock-only-note", "此次结果由本地 Mock 传输生成，未访问外部网络。"));
  body.append(meta, bubble);
  row.append(body);
  messageList.append(row);
  messageList.scrollTo({ top: messageList.scrollHeight, behavior: "smooth" });
}

async function simulateSend(summary, container, target) {
  target.disabled = true;
  target.textContent = "模拟发送中…";
  const triggerPayload = { chatid: summary.chatid, summary_id: summary.id };
  try {
    const trigger = await apiRequest(
      endpoints.mockTrigger,
      { method: "POST", body: JSON.stringify(triggerPayload), debugBody: triggerPayload },
      "创建本地模拟触发消息",
    );
    const sendPayload = { trigger_msgid: trigger.trigger_msgid };
    const sendResult = await apiRequest(
      `${endpoints.summaries}/${summary.id}/send`,
      { method: "POST", body: JSON.stringify(sendPayload), debugBody: sendPayload },
      "模拟发送汇总日报",
    );
    const updated = {
      ...summary,
      publication_status: sendResult.publication_status,
      sent_at: sendResult.sent_at,
    };
    renderConfirmationCard(updated, container, sendResult);
    if (sendResult.attempt.send_status === "sent") {
      renderMockSendChatMessage(updated, sendResult);
      showToast(`汇总快照 ${summary.id} 模拟发送成功。`);
    } else {
      renderSystemMessage("本地模拟发送失败，请查看发送记录；重新确认后可以重试。", "error");
      showToast("模拟发送失败，请查看发送记录并重新确认后重试。", true);
    }
  } catch (error) {
    showToast(error.message, true);
  } finally {
    target.disabled = false;
    target.textContent = "模拟发送";
  }
}

function appendConfirmationForm(summary, container, card, attemptsContainer) {
  const confirmerId = `confirmed-by-${summary.id}`;
  const noteId = `confirmation-note-${summary.id}`;
  const confirmerField = node("label", "field");
  confirmerField.htmlFor = confirmerId;
  confirmerField.append(node("span", "", "确认人 ID"));
  const confirmer = node("input");
  confirmer.id = confirmerId;
  confirmer.name = "confirmed_by";
  confirmer.maxLength = 255;
  confirmer.value = senderUserid.value;
  confirmerField.append(confirmer);

  const noteField = node("label", "field");
  noteField.htmlFor = noteId;
  noteField.append(node("span", "", "确认备注（可选）"));
  const note = node("textarea");
  note.id = noteId;
  note.name = "confirmation_note";
  note.maxLength = 2000;
  note.placeholder = summary.publication_status === "send_failed"
    ? "请说明失败原因核对结果，确认后可重新模拟发送"
    : "例如：已核对三条项目日报及人数、机械汇总";
  noteField.append(note);
  card.append(confirmerField, noteField);

  const actions = node("div", "confirmation-actions");
  actions.append(button(
    summary.publication_status === "send_failed" ? "重新确认后重试" : "我已核对，确认汇总",
    "primary-button",
    async event => {
      const target = event.currentTarget;
      const confirmedBy = confirmer.value.trim();
      if (!confirmedBy) {
        showToast("请填写确认人 ID。", true);
        confirmer.focus();
        return;
      }
      const payload = {
        confirmed_by: confirmedBy,
        confirmation_note: note.value.trim() || null,
      };
      target.disabled = true;
      try {
        const updated = await apiRequest(
          `${endpoints.summaries}/${summary.id}/confirm`,
          { method: "POST", body: JSON.stringify(payload), debugBody: payload },
          "人工确认",
        );
        renderConfirmationCard(updated, container);
        showToast(`汇总快照 ${summary.id} 已完成人工确认。`);
      } catch (error) {
        showToast(error.message, true);
      } finally {
        target.disabled = false;
      }
    },
  ));
  if (summary.publication_status === "send_failed") {
    actions.append(button("查看发送记录", "secondary-button", event => {
      viewSendAttempts(summary, attemptsContainer, event.currentTarget);
    }));
  }
  card.append(actions, attemptsContainer);
}

function renderConfirmationCard(summary, container, sendResult = null) {
  const existing = container.querySelector(".confirmation-card");
  if (existing) existing.remove();

  const card = node("section", "confirmation-card");
  const top = node("div", "detection-top");
  top.append(
    node("h4", "", "人工确认与模拟发送"),
    statusPill(summary.publication_status, publicationLabels),
  );
  card.append(top);
  card.append(node(
    "p",
    "",
    "确认后才能执行本地模拟发送。模拟过程复用现有发送状态机，但只使用 Mock 传输，不会发送真实群聊。",
  ));

  if (summary.confirmed_by) {
    const meta = node("div", "confirmation-meta");
    meta.append(
      node("div", "", `确认人：${display(summary.confirmed_by)}`),
      node("div", "", `确认时间：${timeLabel(summary.confirmed_at)}`),
      node("div", "", `确认备注：${display(summary.confirmation_note, "无")}`),
    );
    card.append(meta);
  }
  if (sendResult) card.append(renderSendOutcome(summary, sendResult));

  const attemptsContainer = node("section", "send-attempts");
  if (["draft", "send_failed"].includes(summary.publication_status)) {
    appendConfirmationForm(summary, container, card, attemptsContainer);
  } else {
    const actions = node("div", "confirmation-actions");
    if (summary.publication_status === "confirmed") {
      actions.append(button("模拟发送", "primary-button", event => {
        simulateSend(summary, container, event.currentTarget);
      }));
    }
    if (summary.publication_status === "sending") {
      const sendingButton = button("模拟发送中…", "primary-button", () => {});
      sendingButton.disabled = true;
      actions.append(sendingButton);
    }
    actions.append(button("查看发送记录", "secondary-button", event => {
      viewSendAttempts(summary, attemptsContainer, event.currentTarget);
    }));
    if (summary.publication_status === "confirmed") {
      actions.append(button("取消人工确认", "secondary-button", async event => {
        const target = event.currentTarget;
        target.disabled = true;
        try {
          const updated = await apiRequest(
            `${endpoints.summaries}/${summary.id}/unconfirm`,
            { method: "POST", debugBody: {} },
            "取消人工确认",
          );
          renderConfirmationCard(updated, container);
          showToast(`汇总快照 ${summary.id} 已退回待确认状态。`);
        } catch (error) {
          showToast(error.message, true);
        } finally {
          target.disabled = false;
        }
      }));
    }
    card.append(actions, attemptsContainer);
  }
  container.append(card);
}

async function saveSummary(event) {
  const saveButton = event.currentTarget;
  saveButton.disabled = true;
  const payload = { chatid: currentChatid, report_date: reportDate.value };
  try {
    const saved = await apiRequest(
      endpoints.summaries,
      { method: "POST", body: JSON.stringify(payload), debugBody: payload },
      "保存汇总快照",
    );
    const previous = summaryResult.querySelector(".snapshot-area");
    if (previous) previous.remove();
    const snapshotArea = node("section", "snapshot-area");
    const notice = node("div", "snapshot-notice");
    notice.append(node("strong", "", `保存成功，summary_id=${saved.id}`));
    notice.append(
      button("查看汇总详情", "ghost-button", async () => {
        try {
          const detail = await apiRequest(`${endpoints.summaries}/${saved.id}`, {}, "查看汇总详情");
          showJson(`汇总详情 · summary_id=${saved.id}`, detail);
        } catch (error) {
          showToast(error.message, true);
        }
      }),
    );
    snapshotArea.append(notice);
    summaryResult.append(snapshotArea);
    renderConfirmationCard(saved, snapshotArea);
    showToast(`汇总快照 ${saved.id} 已保存。`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    saveButton.disabled = false;
  }
}

function selectChat(chatid) {
  currentChatid = chatid;
  chatSelect.value = chatid;
  chatTitle.textContent = groupNames.get(chatid) || chatid;
  for (const item of groupList.querySelectorAll("[data-chatid]")) {
    item.classList.toggle("active", item.dataset.chatid === chatid);
  }
  currentPreview = null;
  summaryResult.replaceChildren();
  const empty = node("div", "empty-state small");
  empty.append(
    node("span", "", "汇"),
    node("p", "", "选择日期后生成预览，机器人会把汇总日报发送到聊天区"),
  );
  summaryResult.append(empty);
  loadChat();
}

composer.addEventListener("submit", event => {
  event.preventDefault();
  sendMessage();
});
messageInput.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});
senderUserid.addEventListener("change", () => {
  const index = senderUserid.selectedIndex;
  if (index >= 0 && index < senderName.options.length) senderName.selectedIndex = index;
});
senderName.addEventListener("change", () => {
  const index = senderName.selectedIndex;
  if (index >= 0 && index < senderUserid.options.length) senderUserid.selectedIndex = index;
});
groupList.addEventListener("click", event => {
  const target = event.target.closest("[data-chatid]");
  if (target) selectChat(target.dataset.chatid);
});
chatSelect.addEventListener("change", () => selectChat(chatSelect.value));
scrollOldestButton.addEventListener("click", () => scrollMessages("oldest"));
scrollLatestButton.addEventListener("click", () => scrollMessages("latest"));
messageList.addEventListener("keydown", event => {
  if (event.ctrlKey && event.key === "Home") {
    event.preventDefault();
    scrollMessages("oldest");
  }
  if (event.ctrlKey && event.key === "End") {
    event.preventDefault();
    scrollMessages("latest");
  }
});
previewButton.addEventListener("click", previewSummary);
document.querySelector("#dialog-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", event => {
  if (event.target === dialog) dialog.close();
});

reportDate.value = localToday();
selectChat(currentChatid);
