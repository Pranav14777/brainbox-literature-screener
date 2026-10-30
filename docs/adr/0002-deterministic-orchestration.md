# ADR 0002: Fixed pipeline in code; LLMs supply validated values, code chooses the next step

- Status: Accepted
- Date: 2026-10-29

## Context

The interviewers flagged "what should be an LLM agent versus a deterministic workflow" as the hardest decision. Routing has to be reliable: the Critic must never be skipped, loops must end, and email must never be sent without approval.

## Decision

The orchestrator is a fixed state machine written in code. It is built in plain Python (ADR 0008):

```
intake -> retrieval -> eligibility -> summarizer -> content checks -> critic
       -> (revise, max 1 per round) -> human review (change requests, max 2) -> send
```

- An LLM never picks the next step. An LLM may produce a value, for example the Critic's verdict or the intake form. Code validates that value against a schema and branches on it.
- Every loop has a counter with a hard cap (ADR 0006, ADR 0007).
- If an LLM output is invalid, it is retried once. If it is still invalid, code uses a safe default:

| Output | Safe default after one retry |
|---|---|
| Intake form | The run stops with a message |
| Eligibility facts for one study | That study becomes `needs_review`, never `include` |
| Summarizer claim failing the content checks | The claim is removed and shown as "removed: unsupported". If no claims pass: "No synthesis could be produced; see evidence table" |
| Critic verdict | The draft goes to human review with the warning "Critic review unavailable" |

- The send function cannot run unless an approval has been recorded for exactly the content being sent (ADR 0007).

## Why

- The order of steps is the same for every question. Only the content changes. An LLM supervisor would add flexibility this pipeline never uses, and the cost is unpredictable paths.
- A fixed path can be tested ("after a Critic rejection, the Summarizer always runs next"). An LLM supervisor's path cannot.
- The expensive mistakes (skipping review, sending early, looping forever) happen in routing. That is where predictability matters most.
- The brief allows this: "Agents may share one LLM; email sending can be a normal function, not an extra agent." The agents are role-specific LLM components, each with its own prompt and input/output contract. Coordination is deliberately left to code.

## Alternatives considered

- **LLM supervisor** (CrewAI hierarchical, AutoGen group chat): rejected. It can skip steps, loop or send early, and takes a different path on each run.
- **Pure code with no LLM judgements:** cannot read free-text abstracts or questions.
