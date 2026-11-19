# Reliability checklist

Checks that earned their place: each one exists because the same mistake showed up more than once. The reviewer applies only the items whose trigger it can see in the diff, usually three to ten per pull request.

| ID | Trigger (readable from a diff) | Check | Origin |
|----|--------------------------------|-------|--------|
| RC-01 | A schema, a serialized type or a stored record format changes | Records written before the change still load: new fields are optional or have a default | PR #___ |
| RC-02 | A log statement is added or changed | No request bodies, tokens or user identifiers are logged | Incident ___ |
| RC-03 | Retry, job or migration code is added or changed | The operation is idempotent: running it twice has the same effect as running it once | PR #___ |

## Adding an item

An item is added through a pull request that a human code owner approves, and only when all of these hold:

- The pattern appeared in at least two pull requests or incidents. Link them in Origin.
- The trigger can be recognized from a diff alone.
- The check says what to look for in one sentence.

Replace the `___` placeholders above with real links, or delete the rows that do not apply to this repository. Validate the table with:

```bash
python3 -I .claude/reviewkit/rk.py checklist lint
```

## Pruning

The monthly review metrics issue lists items that produced no findings in the period. Remove items that no longer earn their place: a checklist only grows if nobody prunes it.
