"use strict";

let snapshot = null;
const $ = (selector) => document.querySelector(selector);
const escapeText = (value) => String(value ?? "");

async function getJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function badge(value, good = false) {
  const kind = good ? "ok" : value === "failed" || value === "invalid" ? "bad" : "warn";
  const span = document.createElement("span");
  span.className = `badge ${kind}`;
  span.textContent = escapeText(value);
  return span;
}

function renderRuns() {
  const query = $("#filter").value.trim().toLowerCase();
  const body = $("#runs");
  body.replaceChildren();
  const runs = snapshot.runs.filter((run) => [run.run_id, run.task_id, run.title, run.state, run.executor_type].join(" ").toLowerCase().includes(query));
  if (!runs.length) {
    const row = document.createElement("tr"); const cell = document.createElement("td");
    cell.colSpan = 6; cell.className = "empty"; cell.textContent = "没有匹配的持久运行"; row.append(cell); body.append(row); return;
  }
  for (const run of runs) {
    const row = document.createElement("tr"); row.tabIndex = 0;
    const values = [
      [run.run_id, `${run.task_id} · ${run.title}`],
      [run.state, run.stage],
      [run.executor_type, run.executor_status],
      [run.artifacts_valid && run.workflow_valid && run.run_record_valid ? "valid" : "invalid", "hash chains + artifacts"],
      [run.review_status || "—", `${run.decision_recorded ? "已记录" : "未记录"} / ${run.decision_applied ? "已应用" : "未应用"}`],
      [`${run.input_tokens + run.output_tokens}`, `cached ${run.cached_input_tokens}`],
    ];
    values.forEach(([primary, secondary], index) => {
      const cell = document.createElement("td");
      if (index === 0) cell.className = "run-id";
      if (index === 1 || index === 3) cell.append(badge(primary, primary === "valid" || primary === "completed" || primary === "merge_review"));
      else cell.append(document.createTextNode(escapeText(primary)));
      const sub = document.createElement("span"); sub.className = "sub"; sub.textContent = escapeText(secondary); cell.append(sub); row.append(cell);
    });
    const open = () => showDetail(run.run_id); row.addEventListener("click", open); row.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); }); body.append(row);
  }
}

async function showDetail(runId) {
  try {
    const detail = await getJson(`/api/runs/${encodeURIComponent(runId)}`);
    $("#detail-title").textContent = `${runId} · 证据时间线`;
    const timeline = $("#timeline"); timeline.replaceChildren();
    detail.workflow.forEach((event) => {
      const item = document.createElement("div"); item.className = "event";
      const seq = document.createElement("strong"); seq.textContent = `#${event.sequence}`;
      const state = document.createElement("span"); state.textContent = `${event.from_state} → ${event.to_state}`;
      const reason = document.createElement("span"); reason.textContent = `${event.actor_type}:${event.actor_id} · ${event.reason}`;
      item.append(seq, state, reason); timeline.append(item);
    });
    const records = $("#run-records"); records.replaceChildren();
    detail.run_records.forEach((record) => {
      const item = document.createElement("div"); item.className = "event";
      const seq = document.createElement("strong"); seq.textContent = `#${record.sequence}`;
      const stage = document.createElement("span"); stage.textContent = record.stage;
      const hash = document.createElement("span"); hash.textContent = `${record.record_hash.slice(0, 16)}…`;
      item.append(seq, stage, hash); records.append(item);
    });
    const events = $("#control-events"); events.replaceChildren();
    (detail.events || []).forEach((event) => {
      const item = document.createElement("div"); item.className = "event";
      const topic = document.createElement("strong"); topic.textContent = event.topic;
      const status = document.createElement("span"); status.textContent = event.published_at ? "published" : "pending";
      const created = document.createElement("span"); created.textContent = event.created_at;
      item.append(topic, status, created); events.append(item);
    });
    if (!(detail.events || []).length) {
      const empty = document.createElement("div"); empty.className = "event"; empty.textContent = "本地兼容记录没有事务 outbox 事件"; events.append(empty);
    }
    const artifacts = $("#artifacts"); artifacts.replaceChildren();
    detail.artifacts.forEach((artifact) => { const item = document.createElement("div"); item.className = "artifact"; item.textContent = `${artifact.artifact_type}\n${artifact.producer}\n${artifact.size_bytes} B · ${artifact.sha256.slice(0, 12)}…`; artifacts.append(item); });
    $("#detail").hidden = false; $("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { $("#error").textContent = `运行详情读取失败：${error.message}`; }
}

async function refresh() {
  try {
    const [session, next] = await Promise.all([getJson("/api/session"), getJson("/api/dashboard")]);
    snapshot = next; $("#identity").textContent = `${session.principal.display_name} · ${session.principal.subject_id}`;
    $("#total-runs").textContent = next.runs.length;
    $("#pending-decisions").textContent = next.runs.filter((run) => run.state === "merge_review" && !run.decision_recorded).length;
    $("#invalid-runs").textContent = next.invalid_runs.length;
    $("#tokens").textContent = (next.total_input_tokens + next.total_output_tokens).toLocaleString();
    $("#error").textContent = next.invalid_runs.length ? `${next.invalid_runs.length} 个运行未通过完整性读取；详情未进入正常表格。` : "";
    renderRuns();
  } catch (error) { $("#live").textContent = "● OFFLINE"; $("#live").className = "bad"; $("#error").textContent = `管理快照读取失败：${error.message}`; }
}

$("#filter").addEventListener("input", renderRuns);
$("#close-detail").addEventListener("click", () => { $("#detail").hidden = true; });
refresh(); setInterval(refresh, 3000);
