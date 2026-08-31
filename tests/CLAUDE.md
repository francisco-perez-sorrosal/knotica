# `tests/` — pytest suite

The full suite is ~2,590 tests and runs in roughly 5–6 minutes. Do not pay that on every edit.

## Scoped runs

```bash
make test-groups                        # list the groups
make test-group GROUP=<id>              # run one, in seconds
make test-group GROUP=<id> ARGS="-x -q"
```

Group membership derives from [`.ai-state/TEST_TOPOLOGY.md`](../.ai-state/TEST_TOPOLOGY.md) and is **never restated elsewhere**. `make verify`'s first step is `scripts/test_group.py --check`, which fails when the topology and the suite drift apart — so a new test file usually means a topology entry too.

## Naming

Test names describe the **behavior under test**, in full sentences. `test_a_heading_bounds_the_block_before_it_even_with_no_blank_line_between` is the house style — long and specific beats short and cryptic.

Never prefix a test with a pipeline identifier (`REQ-`, `AC-`, `Step N`). A pre-commit hook blocks those: they point at documents that get deleted, so the reference dangles. Describe the behavior instead.

## Discipline

- **Characterize before you change.** When fixing a behavioral defect, first write a test that pins the *current* behavior, watch it pass, then change the code and watch it fail. A regression guard you never saw fail is not a guard.
- **Prove the guard bites.** The convention here is to revert the fix, confirm the new test fails, and restore — several existing tests document exactly that.
- **Measure, do not assume.** More than one tech-debt row in this repo was closed by discovering the reported premise had gone stale. Drive the real code path before believing a description of it.
- Architecture invariants are tests too: `tests/test_architecture_boundaries.py` fails the build if anything outside `core.transaction` calls the store's raw write/delete.

## Credentials

Some tests exercise credential-resolution paths and will emit a metered-fallback warning when `ANTHROPIC_API_KEY` is set but `CLAUDE_CODE_OAUTH_TOKEN` is not. No test should ever make a real billed API call — if you are adding one that could, stub the client.
