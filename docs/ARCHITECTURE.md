# Architecture

This document explains how the kit is put together and why. Read the [README](../README.md) first for what the kit does and how to install it.

## The six layers

The kit implements the layers of the verification-first proposal. Each layer catches what the cheaper layer before it cannot.

| Layer | Runs | What it does | Implemented by |
|---|---|---|---|
| L1 Inner loop | In every local Claude Code session, when Claude is about to stop | Runs the project's checks. If they fail, Claude keeps working. | `Stop` hook, `.claude/hooks/run_checks_on_stop.py` |
| L2 End-to-end | Before a commit | Proves the change works the way a user would use it, and records evidence | `verify` skill, `.claude/skills/verify/SKILL.md` |
| L3 Fresh-eyes review | Before a pull request | Reviews the diff in a context that did not write it | Built-in `/code-review`, or `/pr-review` (the same team the CI review uses) |
| L4 Commit gate | On every `git commit` Claude runs | Records whether the exact tree being committed was verified. Blocks only in `enforce` mode. | `PreToolUse` hook, `.claude/hooks/verify_commit_gate.py` |
| L5 Pull request review | On GitHub, on every push to a pull request | Independent finders, a separate verifier, findings posted inline with a neutral check run | `.github/workflows/claude-review.yml` |
| L6 Human approval | On GitHub, before merge | Requires 0, 1 or 2 human approvals of the latest commit, by risk tier | `.github/workflows/risk-approval.yml`, `CODEOWNERS`, branch protection |

Two more workflows connect the layers to issues: `claude.yml` lets people ask `@claude` to work on an issue or a finding, and `issue-intake.yml` checks that a new issue has acceptance criteria. `review-metrics.yml` opens a monthly issue with the numbers that show whether the process works.

## What is in this repository

```text
install.py, installer/     The installer: copies the payload into a target repository, merges
                           the files the target already owns, records a manifest.
kit/                       The payload. Everything under it is copied to the same path in the target.
  .claude/reviewkit/       A Python library (standard library only) and its entry point, rk.py.
  .claude/hooks/           Two thin hook scripts that call the library.
  .claude/skills/          verify, pr-review, bug-finding, finding-verification.
  .claude/agents/          bug-finder, finding-verifier.
  .claude/settings.json    A hooks fragment that the installer merges into the target's settings.
  .github/                 Workflows, an issue form, a pull request template, Dependabot config.
  REVIEW.md, docs/         Starter review rules and a starter reliability checklist.
blocks/                    Text the installer merges into CLAUDE.md, CODEOWNERS and .gitignore.
spec/                      The review team described as a multi-agent spec, linted with mas-architect.
tests/                     The kit's own test suite.
```

All logic lives in one library, `reviewkit`, which is vendored into the target at `.claude/reviewkit/lib/`. Hooks and workflows are thin: they call `python3 -I .claude/reviewkit/rk.py <command>`. The `-I` flag runs Python in isolated mode, so the script ignores environment variables such as `PYTHONPATH` and does not import modules from the current directory. Nothing is installed with `pip`, and the library needs only Python 3.9 or newer and `git`.

One file holds every setting: `.claude/reviewkit.json`. See [CONFIGURATION.md](CONFIGURATION.md).

## Design rules

These rules explain most of the choices below.

1. **The model judges; code acts.** Claude decides whether something is a bug. Everything else is deterministic code with tests: which pull requests to review, what the reviewer may read, whether a finding's quoted code exists, what is posted, which labels change.
2. **A change cannot edit the checks that judge it.** Review rules, skills, subagents, scripts and the workflow itself are read from the base branch. The pull request's own files are data.
3. **Local hooks fail open; CI fails visibly.** A broken hook must never trap a developer, so any internal error lets the action through. A broken CI review must never look like a clean review, so it reports that it did not complete.
4. **State lives in the harness.** A review session starts with no memory. What happened in earlier rounds is stored on the pull request and handed to the session as files.
5. **New gates start in observe mode.** Every blocking behaviour has a mode that only records what it would have done.

## The local loop

### Checks when Claude stops (L1)

The `Stop` hook runs the commands listed in `checks.on_stop` (by default the test command). On failure it tells Claude what failed and Claude continues. Three details keep this from being a trap or a rubber stamp:

- **It runs only when something changed.** The hook fingerprints the working tree. If the tree is unchanged since the session started, or since the checks last passed, it does nothing.
- **Three strikes.** After `checks.max_blocks` failed attempts in one session (default 3) it lets Claude stop and tells the user that a person is needed. This is the proposal's escalation rule.
- **A run that executed no tests is a failure.** Output such as "collected 0 items" or "no tests ran" counts as `zero_run`, because an empty run proves nothing.

### The fingerprint

"Verified" has to mean "this exact tree was verified", and it has to survive the commit itself. The kit computes a fingerprint by copying git's index to a temporary file, adding every tracked and untracked file to that copy, and asking git for the tree id. The real index is never touched. `.claude/state/` is left out, so writing the marker does not change the fingerprint.

### The commit gate (L4)

When Claude runs `git commit`, the `PreToolUse` hook compares the current fingerprint with the one stored by `verify mark`, and reads the `Verified-By:` trailer from the commit message.

| Mode | Behaviour |
|---|---|
| `off` | Does nothing |
| `observe` (default) | Never blocks. Appends what it would have done to `.claude/state/gate.log`. |
| `enforce` | Blocks a commit of an unverified tree, or one without a trailer. After `gate.max_blocks` blocks in a session (default 2) it lets the commit through, so Claude cannot get stuck. |

A commit is always allowed when it carries `Verified-By: none (<reason>)`, or when every changed path matches `gate.exempt_paths` (by default Markdown and `docs/`). The opt-out is visible in git history, which is the point: unverified commits can be counted.

The hook recognises `git commit` inside compound commands and with global options such as `git -C dir commit`. Hooks see only what Claude runs. A person committing from their own terminal is not gated; the trailer convention and the metrics report cover that case.

Both hooks do nothing in CI unless `REVIEWKIT_HOOKS_IN_CI` is set, because a CI job may hold tokens and running the project's test command from a hook there would execute untrusted code.

## The pull request review (L5)

The review is modelled on Anthropic's managed Code Review: find, verify, rank, post. It runs in two jobs.

```text
pull_request_target / issue_comment / workflow_dispatch
        │
        ▼
┌─ job: review (GitHub token can only read) ───────────────────────────────┐
│ 1. check out the BASE branch (trusted rules, skills, scripts)            │
│ 2. rk.py review prepare                                                  │
│      decide: review at all? full or incremental? which lenses?           │
│      export the pull request's tree as sanitized data                    │
│      write the diff, the rules, the checklist, earlier findings as files │
│ 3. claude-code-action runs /pr-review with read-only tools               │
│      lead ─► one bug-finder per lens (in parallel, on different models)  │
│      lead ─► finding-verifier (tries to disprove every candidate)        │
│      returns JSON that must match a schema                               │
└───────────────────────────────┬──────────────────────────────────────────┘
                                ▼
┌─ job: publish (can write to the pull request; never talks to Claude) ────┐
│ 4. rk.py review publish                                                  │
│      re-check every finding's quoted code against the file               │
│      deduplicate against earlier rounds, apply caps                      │
│      post one review with inline comments, resolve fixed threads         │
│      neutral check run, round label, escalation after max_rounds         │
└──────────────────────────────────────────────────────────────────────────┘
```

### Deciding what to review

`review prepare` makes every decision that does not need judgment:

- **Skips.** Automatic reviews skip drafts, forks and bot-authored pull requests. A missing token produces a neutral check that says how to add it. An unchanged diff since the last review (same patch id, for example after a rebase) is not reviewed again.
- **Mode.** The first review is `full`. Later reviews are `incremental`: they concentrate on what changed since the last reviewed commit and do not repeat open findings. After `review.max_rounds` rounds with Important findings still open, the pull request gets the `needs-human` label and automatic reviews pause.
- **Level.** Risk decides how deep a review goes. A change that touches a high-risk path is reviewed at `review.high_risk_level` (default `standard`); every other change at `review.level` (default `quick`). The defaults follow a measurement: on the kit's test fixture one finder found everything three did, at about 60% of the cost (see [EVALS.md](EVALS.md)).
- **Lenses.** A lens is one angle a finder looks through: `correctness`, `contracts`, `tests-evidence`, `security`, `cross-file`. `quick` uses one finder that covers all of them. `standard` uses three finders, plus a `security` finder when the change touches a high-risk path; a change under 50 lines that touches none gets one finder. `deep` uses all five.

Only the latest commit matters. A new push cancels the running review, and `review publish` refuses to post if the head moved while the review ran.

### What the reviewer can see and do

The session runs in a checkout of the base branch, so the skills, subagents and settings it loads are trusted. The pull request's tree is exported next to it as plain files. Before the session starts, the kit removes from that export everything Claude Code would treat as instructions or configuration: `.claude/`, `CLAUDE.md`, `AGENTS.md`, `REVIEW.md`, `.mcp.json` and every symbolic link.

The session's tools are `Read`, `Glob`, `Grep` and `Agent`. It has no shell, cannot edit, cannot reach the network, runs with hooks disabled, and cannot read outside its two directories. The GitHub token in its job can only read. Its single output is a JSON object that must match the schema in `review/schema.py`.

Review rules are assembled the way managed Code Review does it: `REVIEW.md` from the base branch, files it includes with `@path`, and a `REVIEW.md` in a subdirectory for changes under that directory, within the same size limits.

### The review team

| Role | Model (default) | Tools | Job |
|---|---|---|---|
| Lead (`/pr-review` skill) | opus | Read, Glob, Grep, Agent | Dispatch the planned lenses, pass candidates to the verifier unchanged, assemble the result |
| `bug-finder`, one per lens | sonnet; haiku for `tests-evidence`; opus for `security` | Read, Grep, Glob | Find candidate defects through one lens, each with a verbatim quote and a failure scenario |
| `finding-verifier` | opus | Read, Grep, Glob | Try to disprove each candidate against the code; give every one a verdict and a reason |

The shape follows the multi-agent practices in [mas-architect](https://github.com/EmmanuelRTM/mas-architect):

- **Independent generation.** Finders run in parallel, never see each other's output, and run on more than one model, so their blind spots differ.
- **Spend on verification, not on more generators.** One strong, separate judge filters what several cheaper finders propose.
- **The judge is separate from the generators.** It runs on a different model, has its own rubric skill, has no memory of earlier rounds, and receives the candidates verbatim.
- **Every delegation has an owner, a start condition and a done criterion.** The lead's two briefs are static text with those fields.
- **Zero findings is a valid result; a missing result is not.** A lens that did not complete is reported as not completed, never as clean.

The team is also written down as a spec in `spec/pr-review-team.mas.json`, which the mas-architect linter checks. [EVALS.md](EVALS.md) explains how to measure whether the team beats a single reviewer on your own code.

### From model output to posted comments

`review publish` treats the model's output as untrusted input:

1. **Schema.** A result that does not match the schema is a failed review, reported as such.
2. **Quote check.** Every finding must quote code. The kit looks for that quote in the file at the reviewed commit. A finding whose quote is not there is dropped; one whose quote is on a different line is moved to the right line. This catches invented code deterministically.
3. **Placement.** A finding on a line that is part of the diff becomes an inline comment. A finding elsewhere goes into the review body.
4. **Deduplication and caps.** Findings already open from earlier rounds are not posted again. Nits are capped (`review.max_nits`), and so is the total (`review.max_findings`). On later rounds, nits and pre-existing findings outside the new changes are counted but not posted.
5. **Sanitizing.** Secrets are redacted and `@mentions` are defanged, so a finding cannot notify people or leak a credential it read.

The result is one pull request review, always of type "comment": the kit never approves and never requests changes. Each finding is marked 🔴 Important, 🟡 Nit or 🟣 Pre-existing, with a collapsed "Why this was flagged" section. The check run concludes `neutral` and ends with a machine-readable line of severity counts in the same format as managed Code Review, so tooling written for one works with the other.

### Publish modes

| `review.publish` | What happens |
|---|---|
| `shadow` | Findings go to the workflow run summary and one collapsed comment. Nothing inline. Use it to tune the rules before anyone is interrupted. |
| `advisory` (default) | Inline comments, summary, neutral check run |
| `gate` | Same as `advisory`, and the publish job fails while Important findings are open, so it can be a required check |

### State between rounds

The kit stores state in hidden HTML comments on the pull request: one on each finding (its id, severity and round) and one in the summary (the reviewed commit, the round, the counts). It trusts a marker only when the comment was written by the workflow's own bot account. The next round reads them back through the GitHub API together with which threads were resolved and what people replied. Labels show the same state to people: `review-round:N`, `needs-human`, `claude-review:always`.

## Human approval by risk (L6)

`risk-approval.yml` classifies a pull request by the paths it changes (`risk.high_paths`, `risk.low_paths`, everything else is medium) and sets a commit status on the latest commit. The status passes only when enough people with write access approved that exact commit. An approval from an agent or bot never counts. People approve in the GitHub interface or with a `/approve <full sha>` comment. The comment exists for the author of a pull request, who cannot approve it in the interface: the proposal's rule is that the person who directed an agent-written change counts as one of its reviewers. Set `risk.author_can_approve` to `false` if every approval must come from someone else.

Because the status and `CODEOWNERS` are enforced by branch protection, the kit's `doctor --github` command checks that branch protection actually requires them.

## Issues

- **Intake.** `issue-intake.yml` checks that a new issue states acceptance criteria and labels it when it does not. The issue form `.github/ISSUE_TEMPLATE/task.yml` asks for them up front.
- **Work.** Mentioning `@claude` on an issue starts `claude.yml`. The session gets the repository's verification contract appended to its system prompt and may run the configured check commands without asking.
- **Escalation.** `needs-human` marks a pull request where automation stopped.
- **Metrics.** `review-metrics.yml` opens a monthly issue with revert rate, rounds to pass, finding outcomes, unverified commits and approval load.

## Where the ideas come from

| Source | What the kit takes from it |
|---|---|
| The verification-first proposal | The six layers, the principles, the artifacts and their names |
| [Claude Code Review documentation](https://code.claude.com/docs/en/code-review) | Severity levels, `REVIEW.md` semantics, check run format, comment commands, re-review behaviour |
| [claude-code-action](https://github.com/anthropics/claude-code-action) | Running Claude Code in GitHub Actions on a subscription token, structured output |
| [mas-architect](https://github.com/EmmanuelRTM/mas-architect) | The team's topology and the rules for delegation, independence and judging |
| [gstack](https://github.com/garrytan/gstack) (MIT) | Ideas only, no text: no quote means no finding, a reason for every rejection, missing reviewer output is incomplete, replay tests for command detection, tripwire tests for workflows |
