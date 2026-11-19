---
name: setup
description: Install, upgrade or remove the verification-first review kit in the current repository. It adds Claude Code hooks, skills and subagents plus GitHub Actions for pull request review, risk-tiered human approval and issue intake, all running on a Claude subscription token.
argument-hint: "[--set KEY=VALUE ...] [--force] [--uninstall]"
disable-model-invocation: true
allowed-tools: Read Bash(python3 ${CLAUDE_PLUGIN_ROOT}/install.py --dry-run *) Bash(python3 -I ${CLAUDE_PROJECT_DIR}/.claude/reviewkit/rk.py doctor *)
---

You are installing the review kit into the repository at `${CLAUDE_PROJECT_DIR}`. The installer is deterministic and safe to repeat. It creates the kit's own files, merges into files the repository already has (`.claude/settings.json`, `CLAUDE.md`, `CODEOWNERS`, `.gitignore`), and never overwrites a file the repository owns or a kit file that was edited locally.

Extra arguments from the user: `$ARGUMENTS`

## 1. Preview

Run the installer in dry-run mode. It writes nothing.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/install.py" --dry-run --target "${CLAUDE_PROJECT_DIR}" $ARGUMENTS
```

If it says the target is not the top level of a git work tree, stop and tell the user. Otherwise summarise for the user what would be created, merged and skipped.

## 2. Confirm the settings that matter

Read the preview and check these with the user before writing anything:

- **Check commands.** The installer detects the test, lint and build commands. If a detected command is wrong or missing, pass the right one, for example `--set commands.test="npm test"`. Without a test command the Stop hook has nothing to run.
- **Owners.** `--set owners=@org/team,@person` names who must approve changes to the review rules, hooks and workflows. Without owners no CODEOWNERS rules are written.
- **Anything already customised.** If the preview skips a kit file because it was edited locally, ask before using `--force`.

## 3. Install

Run the same command without `--dry-run`, with the settings agreed in step 2:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/install.py" --target "${CLAUDE_PROJECT_DIR}" $ARGUMENTS
```

## 4. Check the result

```bash
python3 -I "${CLAUDE_PROJECT_DIR}/.claude/reviewkit/rk.py" doctor
```

Explain every warning and failure it prints, with the fix it suggests. A warning about uncommitted files is expected at this point.

## 5. Hand over what only a person can do

Tell the user these steps, in this order. Do not do them yourself unless asked.

1. Commit the new files on a branch, open a pull request and merge it. The workflows run from the default branch, so nothing happens on GitHub until this is merged.
2. Run `claude setup-token` in a terminal and save the value as the repository secret `CLAUDE_CODE_OAUTH_TOKEN` (Settings, Secrets and variables, Actions). Never paste the token into this conversation or into a file.
3. Install the Claude GitHub App (https://github.com/apps/claude) if `@claude` should work on issues and pull requests.
4. Protect the default branch: require a pull request, require code owner review, and require the `risk-approval` status check.
5. Run `python3 -I .claude/reviewkit/rk.py doctor --github` to confirm the secret, Issues and branch protection are in place.

Done when the installer and `doctor` have run and the user has the five steps above.
