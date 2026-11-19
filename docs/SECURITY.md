# Security model

The kit puts a Claude subscription token into GitHub Actions and lets a model read pull requests written by other people. This document states what the kit protects, how, and where its protection ends.

## What is being protected

| Asset | Why it matters |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | It is one person's subscription. Whoever holds it can spend that person's usage. |
| The review rules and checks | `REVIEW.md`, the checklist, `.claude/`, `.github/workflows/`. If a change can edit the checks that judge it, the checks mean nothing. |
| The pull request itself | The review must not approve, merge, push or edit anything |
| The repository's contents | The reviewer reads the whole codebase. It must have no way to send it anywhere. |

## Who is trusted

- **People with write access to the repository are trusted.** They can already push branches, edit workflows and read Actions secrets by changing a workflow. The kit does not try to defend against them; it makes their actions visible (CODEOWNERS, trailers, labels).
- **Pull request content is not trusted.** That covers the diff, file contents, the title and description, commit messages, and replies on review threads, including when the author is a colleague. Any of it may contain text written to steer a model.
- **Model output is not trusted.** The reviewer's result is input to deterministic code, never something that is executed or posted as is.

## How each threat is handled

### A pull request edits its own review

The review, approval and `@claude` workflows start only from `pull_request_target`, `issue_comment`, `issues`, `workflow_dispatch` and `schedule`. For all of these GitHub runs the workflow file from the base or default branch, not from the pull request. The job then checks out that same trusted branch, so the scripts, skills, subagents and `REVIEW.md` in use are the merged ones.

The pull request's files are exported beside the checkout as plain data. Before the review session starts, the kit deletes from that export everything Claude Code would load as instructions or configuration: `.claude/`, `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `REVIEW.md`, `.mcp.json`, `.claude.json` and every symbolic link. A pull request that changes one of the protected paths is still reviewed, and the summary says a code owner must look at it.

A test in this repository fails if a kit workflow ever checks out a pull request ref, uses a trigger that runs pull request code while holding the token, or interpolates event text into a script.

### A pull request steals the token

Nothing from the pull request is executed in a job that holds the token. The review job does not install dependencies, build or run tests. Its session has four tools (`Read`, `Glob`, `Grep`, `Agent`), no shell, no network tools, hooks disabled, and no read access outside its two directories. The token is passed only to the `claude-code-action` step.

For stronger isolation, store the token as an environment secret on an environment restricted to the default branch, and uncomment the `environment:` line in the two workflows that use it. The secret is then unavailable to a workflow run from any other branch, which also covers a collaborator who pushes a branch with an edited workflow.

### Text in a pull request tries to steer the reviewer

This cannot be fully prevented, so the design limits what a successful attempt can achieve:

- The session cannot act. It has no write tools, no shell and no network, and the GitHub token in its job is read-only.
- Its only output is JSON checked against a schema.
- Every finding must quote code that really is in the file at the reviewed commit. Invented findings are dropped by code.
- Before posting, secrets are redacted and `@mentions` are defanged.
- The kit never submits an approving review, whatever the model returns.

What remains possible: a pull request could talk the reviewer into staying silent about a real bug. That is why the review is advisory and human approval is a separate gate that the model cannot influence.

### Forged review state

Review state is stored in hidden markers inside comments. Anyone can write a comment that contains a marker, so the kit reads markers only from comments authored by the workflow's own account (`review.bot_login`). Markers inside a pull request description or a person's comment are ignored, and text copied into the review has marker syntax neutralised.

### Faked approvals

The risk gate counts an approval only when it comes from a person with write access, is for the exact head commit, and is the person's latest signal. Accounts marked as bots and the logins in `risk.agent_logins` never count. An `/approve` comment that was edited after posting does not count, because someone else could have edited it. If two open pull requests share a head commit, the status is held back, since a commit status belongs to a commit and would unlock both.

### The local hooks

The hooks run the project's own check commands on the developer's machine, with the same trust as running the tests by hand. They keep their state in `.claude/state/`, which is ignored by git. If a hook hits an internal error it allows the action, so a bug in the kit cannot lock anyone out. In CI the hooks do nothing unless explicitly enabled.

### Supply chain

- Every action is pinned to a full commit SHA, with the version in a comment. The installed Dependabot configuration proposes updates.
- The library uses only the Python standard library and is vendored into the repository. Nothing is downloaded or installed when a hook or workflow runs.
- Scripts run with `python3 -I`, which ignores `PYTHONPATH` and the current directory when importing.
- Check commands that the `@claude` workflow pre-approves are taken from the default branch's configuration, and only when they are plain commands with no shell operators.

## Where the protection ends

Be clear about these limits before relying on the kit.

1. **People with write access can bypass most of it.** A collaborator can set a commit status named `risk-approval` through the API, edit a workflow on a branch, or read a repository secret from a workflow they push. Branch protection with code owner review on `.github/` and `.claude/`, and the environment-secret hardening above, raise the cost. They do not make a malicious maintainer harmless.
2. **`Verified-By` is a record, not proof.** The commit gate sees only commits that Claude makes. A person can type the trailer by hand, and can commit from a terminal without the hook. The trailer makes unverified work countable; it does not make it impossible.
3. **`@claude` sessions can run code.** In `claude.yml` Claude has write tools and may run the project's commands, in a job that holds the token. It starts only for owners, members and collaborators, and from the default branch's workflow file. When you ask `@claude` to work on a pull request, it works on that pull request's branch. Do not ask it to run code from a branch whose author you do not trust.
4. **The reviewer misses bugs.** A review with no findings means the reviewer found nothing. It does not mean the change is correct. The quote check removes invented findings; nothing can add the findings a model failed to make.
5. **The token is personal.** It carries one person's subscription and expires after a year. If that person leaves, replace the secret with a token from someone else. If the token leaks, replace the secret at once and ask Anthropic support how to invalidate the old one: the Claude Code documentation describes how to create this token, not how to revoke it.
6. **Fork pull requests are skipped by default.** A maintainer can request a review of one with `@claude review`. Its files are still only read, never executed, but the review spends the token owner's usage on outside code.

## Reporting a problem

Open an issue in this repository. Do not include tokens or private code in it.
