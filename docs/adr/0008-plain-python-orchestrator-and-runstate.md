# ADR 0008: Plain Python orchestrator with a persisted RunState

- Status: Accepted
- Date: 2026-10-29

## Context

ADR 0002 fixes the pipeline order in code. The remaining choice was how to build it: plain Python, LangGraph, CrewAI or AutoGen.

## Decision

- **Orchestrator.** Plain Python. Each agent is a function. The orchestrator moves through named states with ordinary branches. The human pause is a CLI prompt.
- **RunState.** All run state lives in one explicit Pydantic `RunState` model. Every agent reads from it and writes to it.
- **Run file.** After every step, `RunState` is saved as JSON in `runs/`. That file is the audit trail, and it is also the approval and delivery record.
- **No secrets in the run file.** The run file never contains credentials or tokens.
- **Tracing.** Each agent function carries a tracing decorator (tool chosen in a later ADR). Traces show the process; the run file shows the data.

## Why

- Every routing line is code the author wrote and can explain.
- Saving state after each step costs milliseconds, while each LLM call takes seconds. In return there is a complete record of what happened at every step, including the approval and delivery status the brief requires.

## Alternatives considered

- **LangGraph.** This is the alternative presented in the walkthrough. Its real advantages are checkpointing and runs that can resume after a crash, which the brief does not require at this scale. It also has a trap: when a run resumes after `interrupt()`, the paused node starts again from the beginning. A send placed before the pause would run twice.
- **CrewAI or AutoGen.** Both are built for agents that collaborate and hand work to each other. That contradicts the fixed-order orchestrator in ADR 0002.

## Consequences

- There is no automatic resume. A crashed run is visible in its run file and is not retried automatically. This matters for email delivery (see ADR 0007).
- Run files contain the recipient address. Sample run files shipped with the deliverables must be checked first.
