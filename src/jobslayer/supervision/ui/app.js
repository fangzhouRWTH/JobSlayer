"use strict";

const app = document.querySelector("#app");
const connectionStatus = document.querySelector("#connection-status");
let sessionToken = null;

function setText(selector, value) {
  const node = document.querySelector(selector);
  if (node) node.textContent = value ?? "—";
}

function makeElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderEvidence(items) {
  const container = document.querySelector("#evidence-list");
  container.replaceChildren();
  items.forEach((item) => {
    const article = makeElement("article", "evidence-item");
    const meta = makeElement("div", "evidence-meta");
    meta.append(
      makeElement("span", "evidence-id", item.evidence_id),
      makeElement("span", "evidence-type", item.evidence_type),
    );
    article.append(meta, makeElement("p", "evidence-summary", item.summary));
    if (item.sha256) {
      article.append(makeElement("p", "evidence-hash hash", `sha256 ${item.sha256}`));
    }
    container.append(article);
  });
}

function renderOptions(card) {
  const list = document.querySelector("#option-list");
  list.replaceChildren();
  card.options.forEach((option) => {
    const label = makeElement("label", "option");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "decision-option";
    input.value = option.option_id;
    input.checked = option.option_id === card.default_option_id;
    const body = document.createElement("div");
    const title = makeElement("div", "option-title", option.label);
    if (option.recommended) title.append(makeElement("span", "badge", "推荐"));
    body.append(
      title,
      makeElement("p", "option-description", option.description),
      makeElement("p", "option-consequence", `后果：${option.consequences}`),
    );
    label.append(input, body);
    list.append(label);
  });
}

function renderTimeline(workflow) {
  const timeline = document.querySelector("#timeline");
  timeline.replaceChildren();
  if (!workflow.journal_configured) {
    setText("#journal-notice", "未提供审计日志；界面不会推测任务状态。");
    return;
  }
  setText("#journal-notice", `哈希链已读取，共 ${workflow.transitions.length} 次转换。`);
  workflow.transitions.forEach((record) => {
    const item = document.createElement("li");
    item.append(
      makeElement("span", "timeline-state", `${record.from_state} → ${record.to_state}`),
      makeElement("span", "timeline-meta", `${record.actor_type} · ${record.actor_id}`),
    );
    timeline.append(item);
  });
  if (workflow.transitions.length === 0) {
    timeline.append(makeElement("li", "timeline-meta", "该任务尚无状态转换记录。"));
  }
}

function renderCapabilities(capabilities) {
  const labels = {
    decision_recording: "记录决定文件",
    decision_application: "应用工作流决定",
    git_merge: "本页执行 Git 集成",
    deployment: "触发部署",
  };
  const list = document.querySelector("#capability-list");
  list.replaceChildren();
  Object.entries(capabilities).forEach(([key, enabled]) => {
    const item = document.createElement("li");
    item.append(
      makeElement("span", "", labels[key] ?? key),
      makeElement("span", `capability-value ${enabled ? "" : "no"}`, enabled ? "可用" : "不可用"),
    );
    list.append(item);
  });
}

function showResult(message, kind) {
  const result = document.querySelector("#decision-result");
  result.hidden = false;
  result.className = `result ${kind}`;
  result.textContent = message;
}

function lockForm() {
  document.querySelectorAll("#decision-form input, #decision-form textarea, #decision-form button")
    .forEach((node) => { node.disabled = true; });
}

async function submitDecision(event) {
  event.preventDefault();
  const selected = document.querySelector('input[name="decision-option"]:checked');
  const rationale = document.querySelector("#rationale").value.trim();
  if (!selected || !rationale) {
    showResult("请选择处理方式并填写决定理由。", "error");
    return;
  }
  const button = document.querySelector("#submit-button");
  button.disabled = true;
  try {
    const response = await fetch("/api/decisions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-JobSlayer-Session": sessionToken,
      },
      body: JSON.stringify({
        selected_option_id: selected.value,
        rationale,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error ?? "决定记录失败");
    showResult(
      `决定已记录（${payload.decision.decision_id}），尚未应用到工作流。`,
      "success",
    );
    lockForm();
  } catch (error) {
    showResult(error.message, "error");
    button.disabled = false;
  }
}

function render(snapshot) {
  const fragment = document.querySelector("#review-template").content.cloneNode(true);
  app.replaceChildren(fragment);
  sessionToken = snapshot.submission_token;
  const card = snapshot.card;

  setText("#risk-badge", `风险 · ${card.risk}`);
  if (card.risk === "medium") {
    document.querySelector("#risk-badge").className = "badge warning";
  } else if (card.risk === "high" || card.risk === "critical") {
    document.querySelector("#risk-badge").className = "badge danger";
  }
  setText(
    "#workflow-badge",
    snapshot.workflow.journal_configured
      ? `状态 · ${snapshot.workflow.current_state}`
      : "状态 · 未提供",
  );
  if (snapshot.workflow.card_state_matches === false) {
    const badge = document.querySelector("#workflow-badge");
    badge.className = "badge danger";
  } else if (!snapshot.workflow.journal_configured) {
    document.querySelector("#workflow-badge").className = "badge neutral";
  }
  setText("#card-kind", card.decision_kind);
  setText("#card-title", card.title);
  setText("#decision-required", card.decision_required);
  setText("#task-id", card.task_id);
  setText("#card-id", card.card_id);
  setText("#actor-id", snapshot.actor.actor_id);
  setText("#card-hash", snapshot.card_sha256);
  setText("#why-now", card.why_now);
  if (card.decision_kind === "merge_review") {
    document.querySelector("#integration-boundary").hidden = false;
    setText(
      "#integration-boundary-text",
      "批准决定应用后只进入 Integrating。本页不会应用决定；只有操作员另行显式运行 integrate-run，且补丁、提交树、基线与目标分支复核通过后，才会本地快进并进入 Completed。不会 push 或部署。",
    );
  }
  setText("#evidence-count", `${card.evidence.length} 项`);

  renderEvidence(card.evidence);
  renderOptions(card);
  renderTimeline(snapshot.workflow);
  renderCapabilities(snapshot.capabilities);

  const artifacts = document.querySelector("#artifact-list");
  artifacts.replaceChildren();
  if (card.affected_artifact_ids.length === 0) {
    artifacts.append(makeElement("li", "muted", "决策卡未列出受影响制品。"));
  } else {
    card.affected_artifact_ids.forEach((id) => artifacts.append(makeElement("li", "", id)));
  }

  document.querySelector("#decision-form").addEventListener("submit", submitDecision);
  if (snapshot.decision) {
    showResult(
      `已有决定记录：${snapshot.decision.decision_id}（尚未应用到工作流）。`,
      "success",
    );
    lockForm();
  } else if (!snapshot.capabilities.decision_recording) {
    showResult(
      `决策卡要求状态 ${snapshot.workflow.expected_state}，当前状态为 ${snapshot.workflow.current_state}；已禁止记录。`,
      "error",
    );
    lockForm();
  }
  connectionStatus.textContent = "本地已连接";
  connectionStatus.className = "badge";
}

async function loadSession() {
  try {
    const response = await fetch("/api/session", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error ?? "无法读取审查会话");
    render(payload);
  } catch (error) {
    app.replaceChildren(makeElement("section", "panel", `加载失败：${error.message}`));
    connectionStatus.textContent = "读取失败";
    connectionStatus.className = "badge danger";
  }
}

loadSession();
