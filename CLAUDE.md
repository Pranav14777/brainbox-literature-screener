# Project rules

## Source of truth
- The ADRs in `docs/adr/` are the source of truth for every decision.
- `docs/glossary.md` defines the names to use in code.
- `docs/build-plan.md` is only the order of work. If the build plan and an ADR ever disagree, the ADR wins.

## Deviating from an ADR
- If the code needs to deviate from an ADR, stop and ask the user first.
- If the user agrees, update the ADR in the same step, so docs and code never disagree.

## Environment
- Always use the project's `.venv` for every command (`.venv\Scripts\python.exe`).

## Secrets
- Never read or print `.env`, `credentials.json` or `token.json`.
- Load secrets in code only.

## Git
- Never run `git commit` or `git push`. The user makes all commits.

## Email
- Never run anything that sends a real email without asking the user first.

## Working style
- Log every bug or surprise as one line in `docs/build-notes.md`.
- After each step, explain what you did in a few plain lines.
