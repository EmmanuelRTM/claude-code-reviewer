# Configuration

Every setting lives in one committed file: `.claude/reviewkit.json`. The installer writes it with the defaults below and the commands it detected. Edit it by hand, or set a value while installing:

```bash
python3 install.py --target ../my-repo --set commands.test="npm test" --set gate.mode=enforce
```

A key you leave out keeps its default. An unknown key is ignored with a warning, and an invalid value is an error that names the field. `python3 -I .claude/reviewkit/rk.py doctor` reports both.

CI reads this file from the default branch, never from a pull request. A pull request that edits it changes nothing until it is merged.

## Top level

| Key | Default | Meaning |
|---|---|---|
| `default_branch` | `"main"` | The branch local reviews compare against and `doctor` checks protection for |
| `owners` | `[]` | GitHub users or teams, such as `"@org/leads"`, who must approve changes to the review rules, hooks and workflows. The installer writes them into `CODEOWNERS`. |

## `commands`

Shell commands that prove the project works. Empty means "this project has none".

| Key | Used by |
|---|---|
| `test` | The Stop hook, the `verify` skill, `@claude` sessions |
| `lint`, `build` | The `verify` skill and `checks run`; the Stop hook too if you add them to `checks.on_stop` |
| `start`, `health_check` | The `verify` skill, to boot the application and know when it is up |

The installer detects `test`, `lint` and `build` from files such as `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod` and a `Makefile`, and prints what it chose. Pass `--no-detect` to skip detection, and correct a wrong guess with `--set`.

## `checks`: what runs when Claude stops

| Key | Default | Meaning |
|---|---|---|
| `on_stop` | `["test"]` | Which commands the Stop hook runs, in order. Entries are `test`, `lint` or `build`. |
| `timeout_seconds` | `600` | Time budget shared by all of them |
| `max_blocks` | `3` | Failed attempts in one session before the hook lets Claude stop and asks for a person |
| `zero_run_is_failure` | `true` | Treat a run that executed no tests as a failure |

Keep `on_stop` fast. A slow hook gets disabled by the people it annoys. Put slow suites in CI.

## `gate`: the commit gate

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"observe"` | `off`, `observe` (log only) or `enforce` (block) |
| `max_blocks` | `2` | Blocks in one session before the next commit is let through |
| `require_trailer` | `true` | In enforce mode, also require a `Verified-By:` trailer |
| `exempt_paths` | `["**/*.md", "docs/**"]` | A commit that changes only these paths is never gated |

The environment variable `VERIFY_GATE_MODE` overrides `mode` for one shell, which is useful for trying `enforce` before committing to it.

## `review`: the pull request review

| Key | Default | Meaning |
|---|---|---|
| `trigger` | `"every_push"` | `every_push`; `once` (when the pull request opens or becomes ready); `manual` (only on `@claude review`). A pull request with the `claude-review:always` label is reviewed on every push in all three modes. |
| `publish` | `"advisory"` | `shadow` (run summary and one collapsed comment), `advisory` (inline comments, neutral check) or `gate` (the job also fails while Important findings are open) |
| `level` | `"quick"` | Depth for ordinary changes: `quick` (one finder), `standard` (three lenses; one finder for a change under 50 lines) or `deep` (five lenses) |
| `high_risk_level` | `"standard"` | Depth for a change that touches `risk.high_paths`. At `standard` and `deep` such a change also gets the `security` lens. |
| `skip_risk_tiers` | `[]` | Risk tiers that are not reviewed automatically, for example `["low"]`. Entries are `low` or `medium`. A person can still ask with `@claude review`. |
| `max_rounds` | `3` | Review rounds with Important findings still open before the pull request is labelled `needs-human` |
| `after_escalation` | `"pause"` | `pause` stops automatic reviews until the label is removed or someone asks; `continue` keeps reviewing, Important findings only |
| `models.lead` | `"opus"` | Model of the session that coordinates the review |
| `models.finder` | `"sonnet"` | Model of the finders |
| `models.finder_light` | `"haiku"` | Model of the `tests-evidence` finder |
| `models.finder_deep` | `"opus"` | Model of the `security` finder |
| `models.verifier` | `"opus"` | Model of the verifier. Keep it different from `models.finder`. |
| `effort` | `"medium"` | Reasoning effort of the lead session |
| `max_turns` | `40` | Turn limit of the lead session |
| `max_diff_bytes` | `600000` | A larger diff is not reviewed; the check says so |
| `max_file_diff_lines` | `2000` | A longer per-file diff is cut in the prepared context. The file itself stays readable in full. |
| `max_findings`, `max_nits` | `30`, `5` | Caps on what is posted in one round |
| `skip_bot_authors` | `true` | Do not review pull requests opened by bots automatically |
| `allowed_bots` | `["claude[bot]"]` | Bot accounts whose events may start a review |
| `check_name` | `"Claude PR Review"` | Name of the check run |
| `bot_login` | `"github-actions"` | The account the workflow posts as. State markers are trusted only from it. |
| `instructions_path` | `"REVIEW.md"` | The review rules |
| `checklist_path` | `"docs/reliability-checklist.md"` | The reliability checklist |
| `protected_paths` | `.github/**`, `.claude/**`, `REVIEW.md`, `CLAUDE.md`, the checklist | A pull request that touches these is called out in the review summary as needing a code owner |

Model names are Claude Code aliases (`opus`, `sonnet`, `haiku`) or full model ids. Aliases follow the newest model your subscription offers.

### Choosing a level

Higher levels cost more of the token owner's subscription usage. On the kit's test fixture a `standard` review cost about 1.6 times a `quick` one and found the same bugs (see [EVALS.md](EVALS.md)). That is why ordinary changes default to `quick` and only high-risk changes get the larger team. The fixture is small and easy, so it does not tell you what happens on your code: measure with `review eval` on bugs from your own history, and raise `level` only if `quick` misses bugs that `standard` catches.

## `risk`: human approval by tier

| Key | Default | Meaning |
|---|---|---|
| `high_paths` | `.github/**`, `.claude/**`, `REVIEW.md`, `CLAUDE.md`, the checklist, `**/migrations/**`, `**/auth/**`, `**/billing/**` | A change touching any of these is high risk |
| `low_paths` | `docs/**`, `**/*.md`, `**/*.txt`, `LICENSE` | A change touching only these is low risk |
| `approvals` | `{"low": 0, "medium": 1, "high": 2}` | Human approvals of the latest commit that each tier needs |
| `status_context` | `"risk-approval"` | Name of the commit status. Require it in branch protection. |
| `agent_logins` | `claude`, `github-actions`, `dependabot`, `copilot` | Accounts whose approval never counts, in addition to every account GitHub marks as a bot |
| `author_can_approve` | `true` | Whether a pull request's author may count as one approver by commenting `/approve <full sha>`. This is how the person who directed an agent-written change vouches for it. Set it to `false` to require every approval from someone else. |

Everything that is neither high nor low is medium. A pull request takes the highest tier among its files. A `risk:high` label raises the tier; no label lowers it. Agree on `high_paths` with your team first: it is the setting that decides where people spend their attention.

## `interactive`: `@claude` on issues and pull requests

| Key | Default | Meaning |
|---|---|---|
| `max_turns` | `60` | Turn limit of an `@claude` session |
| `model` | `""` | Model for those sessions; empty uses the default of the action |

## Path patterns

Patterns are anchored at the repository root. `*` matches within one directory, `**` matches any number of directories, and a pattern ending in `/` matches everything below that directory.

## Environment switches

| Variable | Effect |
|---|---|
| `VERIFY_GATE_MODE` | Overrides `gate.mode` (`off`, `observe`, `enforce`) |
| `REVIEWKIT_DISABLE=1` | Turns both local hooks off |
| `REVIEWKIT_HOOKS_IN_CI=1` | Lets the hooks run in CI, where they are off by default |

## Files you own

The installer creates these once and never overwrites them. They are where your team's judgment goes.

| File | Purpose |
|---|---|
| `REVIEW.md` | What Important means here, what not to report, what to always check. Read by the CI review only. |
| `CLAUDE.md` | The verification contract, inside a marked block the installer maintains. Rules here apply to local sessions and to `/code-review`, which does not read `REVIEW.md`. |
| `docs/reliability-checklist.md` | Checks with a trigger readable from a diff. Validate it with `rk.py checklist lint`. |
| `.claude/skills/verify/SKILL.md` | How to prove a change works in this project: how to start the app, what to drive, what counts as evidence |
| `.github/CODEOWNERS` | Who must approve changes to all of the above, inside a marked block |
