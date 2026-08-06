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

## Verification

Run the complete local suite before reporting completion:

```bash
python -m unittest discover -s tests -v
```

When changing workflow rules, include both an allowed-transition test and a rejected-transition test.

