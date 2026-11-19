# claude-code-reviewer

A kit that gives any GitHub repository a verification-first review process built from Claude Code primitives and GitHub Actions. It is modelled on Anthropic's managed [Code Review](https://code.claude.com/docs/en/code-review) and runs entirely on a personal Claude subscription: no API key and no Team or Enterprise plan.

Point the installer at a repository and it adds:

- **A local loop** for every Claude Code session: a Stop hook that keeps Claude working until the tests pass, a `verify` skill that proves a change works end-to-end, and a commit gate that records whether each commit was verified.
- **A pull request review** on GitHub: independent finders, a separate verifier that tries to disprove every finding, and inline comments marked 🔴 Important, 🟡 Nit or 🟣 Pre-existing, with a neutral check run.
- **Human approval by risk**: zero, one or two approvals of the latest commit, depending on which paths a change touches.
- **Issue integration**: an issue form with acceptance criteria, `@claude` on issues and pull requests, a `needs-human` escalation, and a monthly metrics issue.

## Quick start

You need Python 3.9 or newer, git, a Claude subscription, and a GitHub repository with Actions and Issues turned on.

```bash
git clone https://github.com/EmmanuelRTM/claude-code-reviewer
python3 claude-code-reviewer/install.py --target path/to/your-repo --dry-run   # preview
python3 claude-code-reviewer/install.py --target path/to/your-repo --set owners=@your-org/leads
```

Or, from inside Claude Code in the repository you want to set up:

```text
/plugin marketplace add EmmanuelRTM/claude-code-reviewer
/plugin install reviewkit@reviewkit
/reviewkit:setup
```

Then, in the target repository:

1. Commit the new files, open a pull request and merge it. The workflows run from the default branch.
2. Run `claude setup-token` and save the value as the repository secret `CLAUDE_CODE_OAUTH_TOKEN`.
3. Install the [Claude GitHub App](https://github.com/apps/claude) if you want `@claude` on issues and pull requests.
4. Protect the default branch: require a pull request, code owner review and the `risk-approval` status check.
5. Run `python3 -I .claude/reviewkit/rk.py doctor --github` to confirm everything is wired.

[docs/OPERATIONS.md](docs/OPERATIONS.md) has the full setup, a phased rollout and day-to-day use.

## What gets installed

| Layer | File in your repository | What it does |
|---|---|---|
| Contract | `CLAUDE.md` (a marked block) | Definition of done, testing rules, escalation after three failed rounds |
| L1 Inner loop | `.claude/hooks/run_checks_on_stop.py` | Runs your checks when Claude stops; on failure Claude keeps working |
| L2 End-to-end | `.claude/skills/verify/` | Runs the app, drives the changed flows, records evidence |
| L3 Fresh-eyes review | `.claude/skills/pr-review/`, `.claude/agents/` | `/pr-review` runs the CI review team locally; `/code-review` also works |
| L4 Commit gate | `.claude/hooks/verify_commit_gate.py` | Checks the `Verified-By:` trailer and that the exact tree was verified. Starts in observe mode. |
| L5 Pull request review | `.github/workflows/claude-review.yml`, `REVIEW.md`, `docs/reliability-checklist.md` | Reviews every push, incrementally, with rules from the base branch |
| L6 Human approval | `.github/workflows/risk-approval.yml`, `CODEOWNERS` | A `risk-approval` status that passes when enough people approved the latest commit |
| Issues | `.github/workflows/claude.yml`, `issue-intake.yml`, `review-metrics.yml`, an issue form, a pull request template | `@claude`, acceptance criteria, monthly metrics |
| Settings | `.claude/reviewkit.json` | One file for every option: [docs/CONFIGURATION.md](docs/CONFIGURATION.md) |
| Engine | `.claude/reviewkit/` | A vendored Python library (standard library only) that the hooks and workflows call |

The installer never overwrites a file you own. `REVIEW.md`, the checklist, the `verify` skill and your configuration are created once and are yours to edit. Settings, `CLAUDE.md`, `CODEOWNERS` and `.gitignore` are merged, not replaced.

## How the review works

```text
push ─► prepare (code) ─► review (Claude, read-only) ─► publish (code)
        decide, export        finders ─► verifier        re-check quotes, post,
        the PR as data        JSON matching a schema     label, check run
```

- **Rules come from the base branch.** The workflow, `REVIEW.md`, skills and scripts are read from the branch the pull request targets. A change cannot edit the checks that judge it.
- **The reviewer cannot act.** Its session has read tools only, no shell and no network, and a read-only GitHub token. A separate job that never talks to Claude validates the result and posts it.
- **No quote, no finding.** Every finding must quote code that exists at the reviewed commit, or it is dropped.
- **Later rounds build on earlier ones.** Re-reviews look at what changed, resolve fixed threads and do not repeat open findings. After three rounds with Important findings open, the pull request is labelled `needs-human`.
- **It never approves.** The review is advisory by default. Human approval is a separate gate.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains the design, and [docs/SECURITY.md](docs/SECURITY.md) says what it protects and where that protection ends.

## Subscription only

Every Claude call in this kit authenticates with `CLAUDE_CODE_OAUTH_TOKEN`, the token that `claude setup-token` creates from a Claude subscription (Pro, Max, Ultra, Team or Enterprise). Nothing reads `ANTHROPIC_API_KEY`, and a test fails if a workflow ever mentions one.

CI reviews spend the usage of whoever created the token. [docs/OPERATIONS.md](docs/OPERATIONS.md#controlling-subscription-usage) lists the settings that keep that small.

## Status

This is version 0.1.0. What has been checked, and what has not:

**Checked**

- **Unit and integration tests.** About 1,700 tests cover the hooks, the review planner and publisher against a fake GitHub API, the approval gate, issue intake, metrics, the installer and the health check. They pass on Python 3.9, 3.11 and 3.13.
- **Live reviews.** Twenty real review sessions ran against a fixture repository that the installer had set up, with the same session flags as CI. Every session returned a valid result. All planted bugs were found, confirmed and published in every run, and the clean control change never got an Important finding. [docs/EVALS.md](docs/EVALS.md) has the numbers.
- **Install, upgrade and health check** on that fixture repository, with the real installer.
- **Claude Code's own validators** accept the plugin manifest, the marketplace file, the skills and the subagents.

**Not checked**

- **The workflows have never run on GitHub.** They are covered by structural tests and by running the same commands locally, but the first run in your repository is the real test. Start with a dry run from the Actions tab.
- **`actionlint` was not run** on the workflow files.
- **The reviewer's value on real code is unmeasured.** The fixture's bugs are easy. Whether the larger review team is worth its extra usage on your code is a question only your own eval cases can answer.
- **The `deep` level** has unit tests only.

## Documentation

| Document | Read it for |
|---|---|
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Installing, rolling out in phases, daily use, commands, troubleshooting |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every setting in `.claude/reviewkit.json` |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the layers and the review pipeline work, and why |
| [docs/SECURITY.md](docs/SECURITY.md) | The threat model and its limits |
| [docs/EVALS.md](docs/EVALS.md) | Measuring the reviewer on bugs you already know about |

## Developing the kit

```bash
python -m pip install --group dev      # pytest, pyyaml, ruff (pip 25.1 or newer)
python -m pytest                       # the test suite
python -m ruff check . && python -m ruff format --check .
```

The payload under `kit/` is what the installer copies. The library is at `kit/.claude/reviewkit/lib/reviewkit/`; tests are written first and live in `tests/`.

## Credits

- The process follows the proposal "Verification-First Code Review for Our Repository" and the sources it cites.
- [Claude Code documentation](https://code.claude.com/docs/en/code-review) and [claude-code-action](https://github.com/anthropics/claude-code-action) for the behaviour the review mirrors and the way it runs.
- [mas-architect](https://github.com/EmmanuelRTM/mas-architect) for the multi-agent practices behind the review team.
- [gstack](https://github.com/garrytan/gstack) (MIT) for several review and testing ideas. No text or code was copied from it.

## License

No license file has been added yet. Until the repository owner chooses one, the usual default applies: all rights reserved.
