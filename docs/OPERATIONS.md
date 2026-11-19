# Operations

How to install the kit, roll it out without disrupting a team, and run it day to day. Everything here runs on a Claude subscription; no API key is used anywhere.

## Install

You need Python 3.9 or newer and git on the machine that runs the installer. The target repository needs GitHub Actions and Issues turned on.

**From a clone of this repository:**

```bash
python3 install.py --target ../my-repo --dry-run          # preview, writes nothing
python3 install.py --target ../my-repo --set owners=@my-org/leads
```

**From inside Claude Code, in the repository you want to set up:**

```text
/plugin marketplace add EmmanuelRTM/claude-code-reviewer
/plugin install reviewkit@reviewkit
/reviewkit:setup
```

On Claude Code 2.1.275 or later the first two lines can be one: `/plugin install reviewkit --marketplace EmmanuelRTM/claude-code-reviewer`. The setup skill previews the installation, asks before writing, and then runs the health check.

The installer detects your test, lint and build commands, prints every file it creates or merges, and ends with the steps below. It is safe to repeat: running it again upgrades kit files and leaves alone every file you own and every kit file you edited (use `--force` to overwrite those).

## One-time GitHub setup

1. **Merge the installed files into the default branch.** Open a pull request with them and merge it. The workflows run from the default branch, so nothing happens on GitHub before that.
2. **Add the subscription token.** One person, the token owner, runs `claude setup-token` in a terminal and saves the value as the repository secret `CLAUDE_CODE_OAUTH_TOKEN` (Settings, Secrets and variables, Actions). The token is tied to that person's subscription, and every CI review uses their usage. Never commit it or paste it into a chat.
3. **Install the Claude GitHub App** (https://github.com/apps/claude) if you want `@claude` to work on issues and pull requests. The review workflow does not need it.
4. **Protect the default branch.** Require a pull request, require review from code owners, and require the `risk-approval` status check. Turn off "Allow GitHub Actions to create and approve pull requests" under Settings, Actions, General.
5. **Check.** Run `python3 -I .claude/reviewkit/rk.py doctor --github` with a token that has admin access to the repository (`gh auth login` is enough).

To try the review before anyone sees a comment, open the Actions tab, pick "Claude PR Review", choose "Run workflow", enter a pull request number and leave "dry run" on. The findings appear in the run summary only.

## Rollout

New gates start by observing. Move to the next phase when the exit criterion holds, not on a date.

| Phase | Settings | What to watch | Move on when |
|---|---|---|---|
| 0. Baseline | Nothing enforced. Fill in `REVIEW.md`, three to five checklist items, `risk.high_paths`, `owners`. | Run "Review metrics" once to record today's revert rate and approvals per pull request | The numbers are written down |
| 1. Local loop | `gate.mode: observe` (the default) | `rk.py gate report`, shared weekly: how often the gate would have blocked, and why | The would-block events are real misses, not false alarms |
| 2. CI review | `review.publish: shadow`, then `advisory` | Replies on findings: fixed, wrong, intentional, does not matter | Few findings are called wrong for two weeks |
| 3. Enforce | `gate.mode: enforce`; `risk-approval` required; either "require conversation resolution" in branch protection or `review.publish: gate` | Revert rate, rounds to pass | Revert rate is at or below the baseline |
| 4. Improve | Unchanged | The monthly metrics issue | Ongoing |

Two ways to make Important findings binding in phase 3:

- **Require conversation resolution** in branch protection. Every finding thread must be fixed or resolved with a reason before merge. This keeps the review advisory and puts the decision with the author and the approver.
- **`review.publish: gate`** and require the "Claude PR Review" check. The check fails while Important findings are open.

## Day to day

### In a local Claude Code session

Nothing to remember; the contract is in `CLAUDE.md` and the hooks enforce the mechanical parts.

- When Claude finishes a turn, the Stop hook runs the project checks. If they fail, Claude keeps working. After three failed attempts it stops and asks for a person.
- Before committing, Claude runs the `verify` skill: it runs the checks, starts the application, drives the flows the change touches and records the evidence. You can run it yourself with `/verify`.
- `/code-review` reviews the diff in a fresh context. `/pr-review` runs the same finder and verifier team as CI on your working tree, using your own subscription.
- Every commit message ends with `Verified-By: verify, code-review`, or with `Verified-By: none (<reason>)` when verification does not apply.

Each developer should adapt `.claude/skills/verify/SKILL.md` to the project once: how to start the app, which flows matter, what counts as evidence.

### On a pull request

Within a few minutes of a push, the review posts one summary with inline comments:

| Marker | Meaning | What to do |
|---|---|---|
| 🔴 Important | A bug that should be fixed before merge | Fix it and push, or reply with why it is wrong or intended and resolve the thread |
| 🟡 Nit | Worth fixing, not blocking | Your call |
| 🟣 Pre-existing | A bug in code this change did not touch | Usually a follow-up issue |

Later pushes are reviewed incrementally: the reviewer looks at what changed, resolves threads whose finding is fixed, and does not repeat open findings. The label `review-round:N` counts the rounds. After three rounds with Important findings still open, the pull request gets `needs-human` and automatic reviews pause: fixing findings one at a time is not converging, so talk about the approach.

Commands, as the first line of a top-level pull request comment, from someone with write access:

| Comment | Effect |
|---|---|
| `@claude review` | One full review now. Also works on drafts, forks and paused pull requests. |
| `@claude review always` | Review every later push to this pull request, whatever `review.trigger` says (adds the `claude-review:always` label) |
| `@claude review stop` | Stop push-triggered reviews for this pull request. With `review.trigger: every_push` this is refused; convert the pull request to a draft instead. |
| `/approve <sha>` | Approve that exact commit for the risk gate. Give the full 40-character sha; the gate's run summary shows it ready to copy. This is how a pull request's author vouches for commits an agent pushed for them. An edited comment does not count. |
| `/recheck` | Re-evaluate the risk gate, for example after approving in the GitHub interface |

Drafts, forks and bot-authored pull requests are not reviewed automatically.

### Approvals

`risk-approval` shows how many approvals the latest commit still needs: none for low risk, one for medium, two for high (configurable). An approval covers one commit, so a new push needs new approvals. Approvals from bots and agents never count. After approving in the GitHub interface, comment `/recheck`; the gate does not listen to review events on purpose, because those run the workflow from the pull request's own branch.

### Issues

Open work with the "Task" issue form. It asks for checkable acceptance criteria and a risk tier. An issue without criteria gets the `needs-acceptance-criteria` label and one reminder.

Mention `@claude` in an issue or in a pull request conversation to have Claude implement it, fix a finding or answer a question. Those sessions follow the same contract and their pull requests are reviewed like anyone else's.

### Every month

"Review metrics" opens an issue with the revert rate, rounds to pass, what people said about findings, which checklist items produced findings, how many commits carry a trailer, and approvals per pull request. Use it for two jobs:

- **Turn misses into rules.** A mistake that showed up in at least two pull requests, and that can be recognised from a diff, becomes a checklist item. A code owner approves the change.
- **Prune.** Remove checklist items and `REVIEW.md` rules that produced nothing. Rules only grow if nobody removes them.

## Controlling subscription usage

Every CI review spends the token owner's usage. The settings that matter, cheapest first:

| Setting | Effect |
|---|---|
| Drafts are skipped | Open pull requests as drafts while they are in flux |
| `review.skip_risk_tiers: ["low"]` | Documentation-only changes are not reviewed automatically |
| `review.high_risk_level: quick` | One finder even for high-risk changes. Ordinary changes already use one by default. |
| `review.trigger: once` or `manual` | Review when the pull request opens, or only on request |
| `review.max_turns`, job `timeout-minutes` | Hard limits on one review |

A new push cancels a review that is still running, so only the latest commit is paid for. Rotate the token owner on a schedule so that one person's plan does not carry every review: the new owner runs `claude setup-token` and replaces the secret. A token lasts one year; `doctor --github` warns when the secret is older than about eleven months.

## Command reference

Run these from the repository root as `python3 -I .claude/reviewkit/rk.py <command>`.

| Command | Purpose |
|---|---|
| `doctor [--github] [--strict] [--json]` | Check the installation; with `--github`, also the secret, Issues and branch protection |
| `checks run [test lint build]` | Run the configured checks, stopping at the first failure |
| `verify mark --evidence "..."` | Record that the current tree was verified (the `verify` skill does this) |
| `verify status` | Exit 0 when the current tree is verified |
| `gate report [--days N]` | Summarise what the commit gate saw; share this while in observe mode |
| `checklist lint` | Validate the reliability checklist |
| `metrics report [--days N] [--post-issue]` | Print the metrics report; needs a GitHub token |
| `review eval --cases DIR --level LEVEL --runs N` | Score the reviewer on known bugs; see [EVALS.md](EVALS.md). Runs real sessions on your subscription. |
| `fingerprint` | Print the fingerprint of the working tree |

The remaining commands (`review prepare`, `review publish`, `risk evaluate`, `issue intake`, `interactive args`, `hook ...`) are called by the workflows and hooks.

## Upgrade and uninstall

- **Upgrade:** pull this repository and run the installer again. It updates kit files you did not edit, removes files a newer kit no longer ships, and reports what it skipped. A kit hook you adjusted in `.claude/settings.json`, for example with a longer timeout, is kept as you left it.
- **Uninstall:** `python3 install.py --target ../my-repo --uninstall` removes what the installer added, including the kit's hooks from `.claude/settings.json`. Files you own (`REVIEW.md`, the checklist, the `verify` skill, your configuration) stay.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| The check says "Code review not configured" | The `CLAUDE_CODE_OAUTH_TOKEN` secret is missing. Add it. |
| The review did not run on a pull request | It is a draft, comes from a fork, was opened by a bot, or its risk tier is skipped. Comment `@claude review`. |
| The check says "Code review skipped — no new changes" | The push did not change the diff, for example after a rebase |
| The check says the review did not complete | The "Review with Claude" step failed: most often the token expired or the subscription's usage limit was reached. Open the workflow run for the reason, then rotate the token or wait for the limit to reset. An incomplete review is never shown as a clean one. |
| `@claude` does not answer | The Claude GitHub App is not installed, or the commenter is not a member or collaborator |
| `risk-approval` stays pending after an approval | Comment `/recheck`, and check that the approval is for the latest commit |
| The Stop hook keeps running slow tests | Point `commands.test` at a fast subset, or shorten `checks.on_stop` |
| Hooks do nothing | `REVIEWKIT_DISABLE` is set, the session runs in CI, or `disableAllHooks` is on. `doctor` reports each case. |
| A kit file you edited was not upgraded | The installer keeps local edits. Move your change into a file you own, then re-run with `--force`. |
