# Repository instructions

## Product invariant

JobSlayer owns engineering truth. Models and external agents may propose actions and produce artifacts, but they must not own workflow state, permissions, retry policy, validation requirements, or completion decisions.

## Development rules

- Keep the domain contracts provider-neutral. Do not expose SDK-specific agent objects in `jobslayer.domain`.
- All state changes go through `WorkflowKernel.transition`; do not mutate task state directly.
- Preserve the append-only audit trail and its hash-chain verification.
- A task cannot complete without a passing verification report and an authorized approval actor.
- New executor integrations belong behind an adapter protocol and must normalize events while retaining raw logs as artifacts.
- Prefer deterministic tests and structured evidence over natural-language confidence.
- Do not add infrastructure dependencies before the roadmap exit condition that justifies them.
- Append every material development decision and implementation step to `docs/DEVELOPMENT_LOG.md`. Do not silently rewrite earlier entries; add a correction or superseding decision.
- Record durable architectural decisions in `docs/adr/` and link the ADR from the development log.

## Verification

Run the complete local suite before reporting completion:

```bash
./jobslayer check
```

The unified launcher owns the development sequence. Do not replace it with a
partial command when reporting completion; `check` includes the full unittest
suite, compilation, dependency consistency, testbed validation, and Git diff
checks, including the source-controlled BraveNewWorld runbook bindings.

When changing workflow rules, include both an allowed-transition test and a rejected-transition test.

Before reporting implementation completion, update the current development-log entry with changed files, exact verification commands, results, limitations, and the next recommended step.
