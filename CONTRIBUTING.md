# Contributing

Thanks for contributing! This guide covers the branch/commit conventions, how to
run the checks locally, and the review flow.

## Branch and commit conventions

- Branch off the default branch; use a short descriptive branch name
  (`feat/<topic>`, `fix/<topic>`, `docs/<topic>`).
- One logical change per commit. Keep refactors separate from behavior changes.
- Commit message format:

  ```
  <type>: <imperative subject under 50 chars>

  <body — what and why, wrapped at 72 chars, when context is needed>
  ```

  `type` is one of `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.
- Never commit secrets, credentials, or generated build artifacts.

## Running checks locally

Run these before every commit and before opening a PR — they must all pass:

```bash
uv run ruff check . && uv run ruff format .   # format + lint (fix mode)
uv run mypy src/knotica                       # static type check
uv run pytest                                 # test suite
```

A `.pre-commit-config.yaml` wires the linter, formatter, and a secret scanner.
Activate it once after cloning:

```bash
pre-commit install
```

## Pull request / review flow

1. Push your branch and open a PR against the default branch.
2. Ensure CI is green — linting, type-checking, and tests must pass.
3. Keep the PR focused; describe *what* changed and *why*.
4. Address review feedback in follow-up commits; squash on merge if the project
   prefers a linear history.
