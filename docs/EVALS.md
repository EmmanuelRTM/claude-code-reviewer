# Reviewing the reviewer

A reviewer that is never measured drifts: it gets noisier, or it quietly stops finding things. This document explains how to score the kit's reviewer on bugs you already know about, and reports the one measurement made while building the kit.

## What is measured

A **case** is a past change in your repository plus an answer key that says where the bug was. A **control** is a change that merged and stayed stable; the reviewer should report nothing Important on it.

Recall is scored at three stages, so that a missed bug shows where it was lost:

| Stage | Question | If a bug is lost here |
|---|---|---|
| Found | Did any finder report it? | The finders' playbook or the level needs work |
| Confirmed | Did the verifier keep it? | The verifier is too strict, or the finder's evidence was weak |
| Published | Did it survive the quote check and the caps? | The finding quoted code that is not in the file, or the caps are too low |

Controls measure the opposite failure: Important findings on changes that were fine.

## Writing cases

Put one directory per case under `docs/review-evals/`, each with a `case.json`:

```json
{
  "name": "refund-status",
  "base": "9f2c1ab",
  "head": "4e7d0c3",
  "expected": [
    {
      "path": "shop/service.py",
      "lines": [23, 37],
      "summary": "refund_order passes its arguments to set_status in the wrong order"
    }
  ]
}
```

- `base` and `head` are commits, tags or branches in this repository. The review covers what `head` adds on top of the merge base with `base`.
- `expected` lists the known bugs. `lines` is one line or a `[first, last]` range in the file at `head`; a finding within three lines of the range counts.
- A control has `"control": true` and no `expected`.

Good sources of cases are your own reverts, hotfixes and incident fixes: check out the commit that introduced the bug and use the fix to write the answer key. Keep a few cases aside that you never look at while tuning `REVIEW.md`, so that you can tell real improvement from fitting the rules to the test.

## Running

```bash
python3 -I .claude/reviewkit/rk.py review eval --cases docs/review-evals --level standard --runs 3
python3 -I .claude/reviewkit/rk.py review eval --cases docs/review-evals --level quick --runs 3
```

Each run starts a real review session with the same context builder, tools and restrictions as CI, using the Claude login on your machine. It spends your subscription usage: budget roughly one pull request review per case per run. Judge repeated runs, not a single one; model output varies.

Compare levels before choosing one. `quick` is one finder plus the verifier. If `quick` finds what `standard` finds on your cases, `standard` is costing you usage for nothing.

## What was measured while building the kit

The fixture was a small Python order service (four modules, about 150 lines) with the kit installed by the real installer. Three branches were reviewed, each about 60 changed lines:

| Case | Kind | Known bugs |
|---|---|---|
| `split-and-search` | bug | A bill-splitting function that loses the remainder cents although its docstring promises the shares add up; a search query built by pasting user input into SQL |
| `refunds` | bug | A refund that passes its arguments to the storage layer in the wrong order, so the order is never marked refunded. The tests pass because none of them looks at the stored status. |
| `list-open` | control | None: a correct, tested change |

Each case was reviewed three times at each of two levels, 18 sessions in all, with the default models and the same session flags as CI.

| | `quick` (one finder + verifier) | `standard` (three lenses + verifier) |
|---|---|---|
| Sessions that returned a valid result | 9 of 9 | 9 of 9 |
| Known bugs found | 9 of 9 | 9 of 9 |
| Known bugs confirmed by the verifier | 9 of 9 | 9 of 9 |
| Known bugs published after the quote check | 9 of 9 | 9 of 9 |
| Important findings that were not a known bug | 0 | 0 |
| Controls flagged with an Important finding | 0 of 3 | 0 of 3 |
| Nits per review | 0 | 0 to 3, all about missing tests |
| Time per review | 16 to 56 seconds | 60 to 102 seconds |
| Cost per review (API-equivalent, as reported by Claude Code) | $0.15 to $0.35, mean $0.28 | $0.37 to $0.56, mean $0.46 |

What this shows:

- **The pipeline works end to end.** Every session loaded the skills and subagents, ran each finder on its assigned model, returned output that matched the schema, and needed no tool it was denied. Every known bug survived the deterministic quote check.
- **Verification did its job on duplicates.** Where two finders reported the same bug, or a finder gave a position in the diff instead of a line number, the verifier merged or corrected them.
- **On this fixture the extra finders bought no extra bugs.** `quick` found everything `standard` found. `standard` cost about 1.6 times as much, took about twice as long, and added only nits about test coverage.

What it does not show:

- **It says little about your code.** Three planted bugs in a tiny, clean codebase are easy. Both levels hit the ceiling, so the fixture cannot tell them apart. Subtle bugs that span files, in a large codebase, are where independent finders are expected to help, and that was not measured.
- **Nine sessions per level is a small sample.** It is enough to show the pipeline is stable, not to estimate a miss rate.
- **`deep` and the `security` lens were not part of this comparison.**

This result is why the kit ships with `review.level: quick` for ordinary changes and keeps the multi-lens team for changes that touch high-risk paths (`review.high_risk_level: standard`). Raise the level for ordinary changes only if your own cases show `quick` missing bugs that `standard` catches.

The review team is also described as a spec in `spec/pr-review-team.mas.json`. The [mas-architect](https://github.com/EmmanuelRTM/mas-architect) linter passes it with one warning that follows from the numbers above: the single-finder baseline already scores at the ceiling on this fixture, so the team's benefit is still unproven.

## From a miss to a rule

When the reviewer misses a bug that reached production:

1. Add it as a case, so the miss is measured from now on.
2. If the same kind of mistake has shown up in at least two pull requests, and it can be recognised from a diff, add a checklist item with those pull requests as its origin. A code owner approves the change.
3. If the miss was a matter of judgment specific to your code, add a sentence to `REVIEW.md` under "Always check".
4. Re-run the eval. Keep the change only if recall improves and the controls stay clean.

The monthly metrics issue lists checklist items that produced no findings. Remove them. A rule that never fires costs reading time on every review.
