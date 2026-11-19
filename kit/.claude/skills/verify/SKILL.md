---
name: verify
description: Use before committing any change to application code. Runs the project's checks, launches the app, drives the flows the change touches end-to-end the way a user would, reports the evidence for each flow, and records the verification so the commit gate can see it.
allowed-tools: Read Grep Glob Bash(python3 -I ${CLAUDE_PROJECT_DIR}/.claude/reviewkit/rk.py *)
---

# Verify the change by running it

Passing tests show the code does what the tests say. This skill shows the change does what the user needs: launch the real thing, use it, and look at what happens. Work through the steps in order.

## 1. Scope

Run `git status --short` and `git diff HEAD` and list the user-visible flows the change touches: a page, an endpoint, a command, a job, a migration. If the change touches none (documentation, comments, a rename with no behavior change), say so, skip to step 6 and record why.

Pick the surface before anything else:

- **Browser**: pages and interactions. Drive them in a real browser.
- **Functional**: an API, a CLI, a worker, a webhook, a migration. Drive them with real requests or commands.
- **Both**, when the change crosses them.

## 2. Cheap checks first

Run the project's own checks. Stop here if they fail: fix the cause, then start again.

```bash
python3 -I "${CLAUDE_PROJECT_DIR}/.claude/reviewkit/rk.py" checks run
```

## 3. Launch from a clean state

Use the project's recipe below. If it is empty or wrong, find out how the project starts (a `run-*` skill in `.claude/skills/`, the README, the package scripts, a Makefile), get it running, and then update the recipe in this file so the next run does not have to rediscover it.

<!-- project-recipe:begin -->
- Start command: _not recorded yet_
- Ready when: _not recorded yet (a health URL, a log line, a port)_
- Test data or accounts: _not recorded yet_
- Stop command: _not recorded yet_
<!-- project-recipe:end -->

Use fixtures and local services you own. A local address can still point at production: never create, change or delete data anywhere you did not start yourself.

## 4. Drive each flow

For every flow from step 1, one at a time:

1. Show the success path first, the way a user would reach it, and check the lasting effect (the row written, the file created, the state changed), not only the response. A 200 response is not proof.
2. Then try what the change makes risky: the empty input, the invalid input, the second click, the missing permission, the old record.
3. Compare what you saw with what the issue or the pull request says should happen.

If something fails, reproduce it once more from the same starting state before changing any code. After any code change, every earlier result is stale: run the affected flows again.

## 5. Report the evidence

One row per flow, with what you actually observed:

| Flow | How it was driven | Observed | Result |
|------|-------------------|----------|--------|
| ...  | the exact command, request or clicks | the output, status, screenshot path, stored value | pass, fail, blocked, not run |

Say what kind of evidence each row is: a live run, a test, or a read of the code. A mock is not a live run. Say what you did not verify.

## 6. Record the verification

Only when every flow passed and nothing changed since the last run:

```bash
python3 -I "${CLAUDE_PROJECT_DIR}/.claude/reviewkit/rk.py" verify mark --evidence "<one line: what you ran and saw>"
```

The record is tied to the exact content of the working tree. Any later edit makes it stale, and the commit gate will ask for verification again.

End the commit message with `Verified-By: verify` (add `, code-review` when `/code-review` also ran on the final diff).

## 7. When it does not pass

Fix the cause and repeat from step 2. After three failed rounds, stop and ask a person. Report:

- **Status**: blocked
- **What fails**: the flow and the exact error
- **What you tried**: each attempt and its result
- **What you recommend**

## Rules

- Never skip, weaken, delete or retry a test to get a pass, and never edit a checker's configuration to quiet it.
- Do not call a failure pre-existing unless you saw it fail before your first edit, or on the base branch.
- If you found a bug while verifying, write the test that fails because of it before you fix it.
- Do not record a verification you did not perform.
