"""Installer for the review kit: copies and merges the `kit/` payload into a target repository.

Layers, from the bottom up:

* pure decisions that take existing content and return what to do: `payload`
  (who owns a path), `kitfiles` and `manifest` (kit-owned files), `settings_merge`,
  `blocks` and `templates` (managed blocks), `configgen` (the generated configuration);
* read-only planning that gathers the target's state into a `Plan`:
  `install_plan`, `uninstall_plan`, `planning`, `detect`, `gitquery`;
* a thin layer that applies a plan to disk: `operations`, on top of `fsops`;
* the command line (`cli`) and the printed report (`report`).

Entry points: `installer.cli.main`, `installer.operations.install` and
`installer.operations.uninstall`. Nothing from the target repository is ever executed.
"""
