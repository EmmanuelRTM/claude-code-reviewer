# Review instructions

These rules apply to automated pull request review only. They are read from the protected base branch, never from the pull request under review, so a change cannot rewrite the rules that judge it. Project-wide rules that should also apply while coding belong in `CLAUDE.md`.

## What Important means here

Reserve Important for findings that would break behavior, leak data, or block a rollback: incorrect logic, unscoped data access, secrets or personal data in logs, and migrations that are not backward compatible. Style, naming and refactoring suggestions are Nit at most.

## Evidence

Every finding needs a `file:line` citation, the code it points at quoted word for word, and a one-sentence failure scenario that names the input or caller that reaches it. Behavior claims must come from the source, not from naming.

## Cap the nits

Report at most five Nits per review.

## Do not report

- Anything CI already enforces: lint, formatting, type errors
- Generated files and lockfiles
- Test-only code that intentionally violates production rules

## Always check

- Items in `docs/reliability-checklist.md` whose trigger matches the diff
- New endpoints and user flows have an end-to-end test or an updated step in the `verify` skill
- A bug fix comes with a regression test
- Tests, thresholds or checks that were removed, skipped or loosened in this change are called out
- Changes to `.claude/`, `REVIEW.md`, `docs/reliability-checklist.md` or `.github/workflows/` are flagged for a human code owner

## Summary format

Start the summary with the count of findings by severity.
