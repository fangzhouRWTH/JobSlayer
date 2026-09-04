import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  FileSearch,
  LoaderCircle,
  MessageSquareText,
  Send,
  ShieldAlert,
  UserRound,
} from "lucide-react";
import type {
  TaskManagerHumanActionGuidance,
  TaskManagerHumanDecisionOption,
  TaskManagerHumanInteraction,
} from "../../types";

const actorLabels: Record<TaskManagerHumanActionGuidance["permitted_actor_types"][number], string> = {
  human: "授权人类",
  agent: "独立 Agent",
  policy: "命名 Policy",
  system: "系统",
};

interface InteractiveHumanActionProps {
  busy: boolean;
  canRecordFeedback: boolean;
  canAskAgent: boolean;
  interactions: TaskManagerHumanInteraction[];
  canSubmitDecision: (
    guidance: TaskManagerHumanActionGuidance,
    decision: TaskManagerHumanDecisionOption,
  ) => boolean;
  isFormalDecision: (decision: TaskManagerHumanDecisionOption) => boolean;
  onSubmitDecision: (
    guidance: TaskManagerHumanActionGuidance,
    decision: TaskManagerHumanDecisionOption,
    rationale: string,
  ) => void;
  onRecordFeedback: (
    guidance: TaskManagerHumanActionGuidance,
    decision: TaskManagerHumanDecisionOption,
    content: string,
  ) => void;
  onAskAgent: (guidance: TaskManagerHumanActionGuidance, content: string) => void;
}

export function HumanActionGuidanceCard({
  guidance,
  compact = false,
  interactive,
}: {
  guidance: TaskManagerHumanActionGuidance;
  compact?: boolean;
  interactive?: InteractiveHumanActionProps;
}) {
  const [selectedDecisionId, setSelectedDecisionId] = useState(
    guidance.decisions[0]?.decision_id ?? "",
  );
  const [rationale, setRationale] = useState("");
  const [agentMessage, setAgentMessage] = useState("");
  const [confirmedBoundary, setConfirmedBoundary] = useState(false);
  const selectedDecision = useMemo(
    () => guidance.decisions.find((item) => item.decision_id === selectedDecisionId)
      ?? guidance.decisions[0],
    [guidance.decisions, selectedDecisionId],
  );
  const formal = Boolean(
    selectedDecision && interactive?.isFormalDecision(selectedDecision),
  );
  useEffect(() => {
    setSelectedDecisionId(guidance.decisions[0]?.decision_id ?? "");
    setRationale("");
    setAgentMessage("");
    setConfirmedBoundary(false);
  }, [guidance.guidance_id, guidance.decisions]);

  return (
    <section className={`human-action-guide ${compact ? "compact" : ""}`} aria-label={guidance.title}>
      <header>
        <span><AlertTriangle size={15} /> 需要人工处理</span>
        <small>{guidance.kind.replaceAll("_", " ")}</small>
      </header>
      <h3>{guidance.title}</h3>
      <p>{guidance.summary}</p>
      <div className="human-action-meta">
        <span><UserRound size={13} /> {guidance.permitted_actor_types.map((item) => actorLabels[item]).join(" / ")}</span>
        <span><ShieldAlert size={13} /> {guidance.required_capability}</span>
        <span>PLAN R{guidance.expected_plan_revision}{guidance.expected_run_revision ? ` · RUN R${guidance.expected_run_revision}` : ""}</span>
      </div>

      <div className="human-action-columns">
        <section>
          <h4>处理前要求</h4>
          <ul>{guidance.requirements.map((item) => <li key={item}><CheckCircle2 size={12} /> <span>{item}</span></li>)}</ul>
        </section>
        <section>
          <h4>详细步骤</h4>
          <ol>{guidance.steps.map((item, index) => <li key={item}><b>{index + 1}</b><span>{item}</span></li>)}</ol>
        </section>
      </div>

      {!compact && guidance.evidence_to_review.length > 0 && (
        <section className="human-action-evidence">
          <details>
            <summary><FileSearch size={13} /> 验收证据引用（{guidance.evidence_to_review.length}）</summary>
            <p>这些引用用于绑定服务端验证与审计记录；请结合节点验证摘要和实际交付物核对，不要仅凭 ID 批准。</p>
            <div>{guidance.evidence_to_review.map((item) => <code key={item}>{item}</code>)}</div>
          </details>
        </section>
      )}

      <section className="human-action-decisions">
        <h4>允许的决定</h4>
        <div>{guidance.decisions.map((item) => (
          <article key={item.decision_id} className={selectedDecisionId === item.decision_id ? "selected" : ""}>
            {interactive && (
              <input
                type="radio"
                name={`decision-${guidance.guidance_id}`}
                value={item.decision_id}
                checked={selectedDecisionId === item.decision_id}
                onChange={() => setSelectedDecisionId(item.decision_id)}
                aria-label={`选择 ${item.label}`}
              />
            )}
            <strong>{item.label}</strong>
            <p>{item.effect}</p>
            {item.command && <code>{item.command}</code>}
          </article>
        ))}</div>
      </section>

      <footer>
        <strong>禁止绕过</strong>
        {guidance.prohibited_actions.map((item) => <span key={item}>{item}</span>)}
      </footer>

      {!compact && interactive && selectedDecision && (
        <section className="human-action-control" aria-label="人工确认与反馈">
          <header>
            <div>
              <span>HUMAN DECISION</span>
              <h4>{formal ? "正式确认" : "记录反馈"}</h4>
            </div>
            <small>{formal ? "会调用受治理命令" : "不改变节点状态"}</small>
          </header>
          <textarea
            value={rationale}
            onChange={(event) => setRationale(event.target.value)}
            placeholder={formal
              ? "说明你核对了哪些证据、为何接受，以及本次授权不包含哪些外部动作…"
              : "详细说明缺失证据、需要修改的内容和再次验收条件…"}
          />
          {formal && (
            <label className="human-action-boundary-check">
              <input
                type="checkbox"
                checked={confirmedBoundary}
                onChange={(event) => setConfirmedBoundary(event.target.checked)}
              />
              <span>我已核对可见的验证摘要与实际交付物，并确认该决定只作用于所示 Plan/Run revision，不扩大到未列明的外部动作。</span>
            </label>
          )}
          <div className="human-action-control-footer">
            <small>
              {formal && !interactive.canSubmitDecision(guidance, selectedDecision)
                ? "当前登录身份缺少此正式决定所需的 capability。"
                : "提交后请刷新核对新 revision、节点状态和审计记录。"}
            </small>
            <button
              className={`button ${formal ? "button-primary" : "button-quiet"}`}
              type="button"
              disabled={
                interactive.busy
                || rationale.trim().length < 8
                || (formal && (
                  !interactive.canSubmitDecision(guidance, selectedDecision)
                  || !confirmedBoundary
                ))
                || (!formal && !interactive.canRecordFeedback)
              }
              onClick={() => {
                if (formal) {
                  interactive.onSubmitDecision(guidance, selectedDecision, rationale.trim());
                } else {
                  interactive.onRecordFeedback(guidance, selectedDecision, rationale.trim());
                }
              }}
            >
              {interactive.busy ? <LoaderCircle size={14} /> : <Send size={14} />}
              {formal ? selectedDecision.label : "提交反馈"}
            </button>
          </div>
        </section>
      )}

      {!compact && interactive && (
        <section className="human-action-assistant" aria-label="任务绑定 Agent 辅助">
          <header>
            <span><Bot size={14} /> AGENT ASSIST</span>
            <small>只读解释 / 起草反馈 · 不可批准</small>
          </header>
          {interactive.interactions.length > 0 && (
            <div className="human-action-thread">
              {interactive.interactions.map((item) => (
                <article key={item.interaction_id} className={item.actor_type}>
                  <span>{item.actor_type === "human" ? <UserRound size={13} /> : <Bot size={13} />}</span>
                  <div>
                    <strong>{item.actor_id}</strong>
                    <p>{item.content}</p>
                    <small>PLAN R{item.based_on_plan_revision} · RUN R{item.based_on_run_revision}</small>
                  </div>
                </article>
              ))}
            </div>
          )}
          <textarea
            value={agentMessage}
            onChange={(event) => setAgentMessage(event.target.value)}
            placeholder="请解释某项证据、比较风险，或帮我起草一段明确的暂不批准反馈…"
          />
          <div>
            <small>问题和回答都会绑定当前 guidance 并写入 run 哈希链。</small>
            <button
              className="button button-quiet"
              type="button"
              disabled={interactive.busy || !interactive.canAskAgent || agentMessage.trim().length < 2}
              onClick={() => interactive.onAskAgent(guidance, agentMessage.trim())}
            >
              {interactive.busy ? <LoaderCircle size={14} /> : <MessageSquareText size={14} />}
              询问 Agent
            </button>
          </div>
        </section>
      )}
    </section>
  );
}
