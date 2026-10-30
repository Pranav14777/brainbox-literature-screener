# ADR 0001: CLI only; required deliverables before the stretch goal

- Status: Accepted
- Date: 2026-10-29

## Context

The build is due Thursday night and the viva is Friday. The brief marks Streamlit and the Claude Skill + PubMed connector work as optional. Everything else is required: live search, RAG, four agents, human approval, one real Gmail delivery, mocked failures, the evaluation table, traces and the README.

## Decision

- Use a CLI only. Drop Streamlit completely.
- Build and test every required item first.
- Start the stretch goal (Claude Skill + PubMed connector) only if all required items are done and tested by Thursday 18:00.
- Set up Gmail OAuth (Cloud project, consent screen, test user, `credentials.json`) before any agent work, because it is the step most likely to block progress.

## Alternatives considered

- **Streamlit UI.** The brief says a CLI is enough. A UI adds code and failure modes without adding to any evaluation criterion.
- **Stretch goal in parallel.** Splits limited time across optional work while required items are still unproven.

## Consequences

- The approval step and delivery evidence are shown as CLI screenshots or transcripts.
- If the 18:00 cutoff passes, the README states that the stretch goal was not attempted and why.
