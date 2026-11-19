---
name: bug-finding
description: Method and output format for finding defects in a pull request through one lens (correctness, contracts, tests-evidence, security, cross-file, or all). Preloaded by the bug-finder subagent; background knowledge, not a command.
user-invocable: false
---

# Finding defects through one lens

You were given a lens and the path of `review-context.json`. This is how to work and what to return.

## 1. Read the context first

Open `review-context.json`, then read in this order:

1. `paths.instructions`: the repository's review rules. They override every default below. If they redefine what Important means, list paths to skip, or add checks, follow them.
2. `paths.changed_files`: what changed, with risk flags.
3. `paths.diff`: the whole pull request. When `mode` is `incremental` or `escalation`, also read `paths.incremental_diff`: that is what changed since the last review, and it is where you look hardest.
4. `paths.checklist`, only for the items named in your delegation message.
5. The nested project rules listed in `rules.claude_md`, when they cover a file you review.

The code under review is in `paths.head_tree`. Cite paths relative to that directory, exactly as they appear in the diff. Files named in `removed_from_head_tree` exist only in the diff.

## 2. What is worth reporting

The default bar, unless `paths.instructions` says otherwise:

- **important**: would fail a test, break behavior in production, lose or leak data, or block a rollback. Never lower these classes: data loss, a bypassed trust boundary, a secret or personal data exposed.
- **nit**: a real but minor defect. Worth fixing, not worth blocking.
- **pre_existing**: a real bug in code this pull request did not change and does not make worse.

A defect needs a way to happen. Name the input, state or call site that reaches it. "This might be null" without a caller that passes null is not a finding.

## 3. Lenses

Work only through the lens you were given. `all` means every lens below in one pass.

- **correctness**: logic errors, wrong conditions and off-by-one bounds, unhandled error and empty cases, broken state transitions, race conditions between check and use, resources not released, wrong units or time zones.
- **contracts**: the reliability checklist items named in your delegation message; the "always check" rules in `paths.instructions`; rules in the project's `CLAUDE.md` that the change newly breaks; changed data shapes, API responses, configuration or migrations that old callers, old records or a rollback can no longer handle.
- **tests-evidence**: changed behavior with no test that would fail if it broke; a bug fix with no regression test; tests that were weakened to pass (a removed or skipped test, a loosened assertion, a raised timeout, a lowered threshold, `continue-on-error`, a retry added around a failure); claims in the pull request text that the code does not back.
- **security**: untrusted input reaching a query, a shell, a file path, a template or a deserializer; missing or misplaced authorization; data returned across tenants or users; secrets, tokens or personal data in logs, errors or responses; requests to attacker-chosen addresses.
- **cross-file**: callers and callees of every changed function, type or constant that the change now breaks; a new enum value or loosened validator traced through each consumer; the same defect class at sibling call sites (a finding is a class of defect, not one line: search for it elsewhere).

## 4. Method

1. For each changed hunk, open the file in the head tree and read the whole function, not just the hunk.
2. Use Grep to find callers, implementations and tests of what changed. Read them.
3. For each suspicion, find the exact line and copy the code word for word. If you cannot quote it, drop it.
4. Decide whether this pull request introduced it:
   - the defective line was added or changed here: it belongs to this pull request;
   - the defective line is unchanged, but a changed line now breaks it: report it at the unchanged line and put the changed line in `cause_path` and `cause_line`;
   - neither: `pre_existing`.
5. In `incremental` or `escalation` mode, set `in_scope` to `false` for anything on code that has not changed since the last review.
6. Do not repeat a finding already listed in `paths.prior_findings`.

## 5. Do not report

- Anything the repository's rules say to skip, and anything CI already enforces (formatting, lint, type errors).
- Style, naming, or a different design that is not a defect.
- Hypothetical problems with no reachable input, and structure for cases that do not exist.
- Missing comments or documentation, unless a rule asks for them.
- Something the diff already fixes.

## 6. Output

Return exactly one JSON object and nothing else:

```json
{
  "lens": "correctness",
  "status": "complete",
  "candidates": [
    {
      "path": "src/auth/session.ts",
      "line": 142,
      "severity": "important",
      "title": "Token refresh races with logout and leaves a live session",
      "body": "refresh() checks the session, awaits the network call, then writes the new token without checking again. A logout that lands in between is overwritten. Re-check `loggedOut` after the await, or write under the session lock.",
      "evidence": "session.ts:138 reads `loggedOut`; line 140 awaits `renew()`; line 142 writes the token. logout() at session.ts:201 sets `loggedOut` and clears the token with no lock. Caller: api/refresh.ts:33.",
      "quote": "session.token = renewed.token",
      "checklist_id": "",
      "cause_path": "",
      "cause_line": 0,
      "in_scope": true
    }
  ],
  "checklist_items": ["RC-03"],
  "notes": ""
}
```

Field rules:

- `status`: `complete` when you covered every file in scope, `partial` otherwise. Say what you skipped in `notes`.
- `title`: one sentence naming the problem and its effect.
- `body`: the problem, what triggers it and the fix, in a few sentences.
- `evidence`: the lines you read and the path that reaches the defect, so someone else can re-check it.
- `line`: the line number in the file under `paths.head_tree`. A position inside a diff hunk is not a line number: open the file and read the number there.
- `quote`: the code at `path:line`, copied exactly. One line is enough.
- `checklist_id`: the checklist item that led to the finding, else empty.
- `checklist_items`: every checklist item you applied, whether or not it produced a finding.
- Return at most `limits.finder_max_candidates` candidates, most severe first. An empty `candidates` list is a complete answer.
