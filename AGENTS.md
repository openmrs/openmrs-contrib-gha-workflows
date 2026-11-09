# Notes for coding agents

These workflows are called by many repositories, inside and outside the OpenMRS organization, that this repository
cannot test directly. Callers rely on the documentation site in [`docs/`](docs) to know what the workflows do.

## Keep the documentation in sync

Every change to a workflow, composite action or script must be checked against the documentation, and the docs
updated in the same change. Before finishing, verify:

- **Inputs, secrets and outputs.** The reference tables are generated from the `description` fields in the workflow
  and action YAML, so update those descriptions rather than the pages.
- **Behaviour described in prose.** Search `docs/content` for the input, step or behaviour you changed (defaults,
  branches, guards, failure modes, permissions) and update every page that describes it, including
  `docs/content/community/examples.mdx` and the OpenMRS and community getting-started pages.
- **Examples.** Example caller workflows must only use inputs and secrets that exist, and the permissions they grant
  must cover what the called jobs request.

## Checks

Run these before declaring a change done:

- `python3 -m unittest discover -s .github/tests`
- `ruff check .github`
- `actionlint` (warnings about `job.workflow_repository` and `job.workflow_sha` are expected)
- `shellcheck .github/scripts/*.sh`
- `pnpm build` in `docs/`, which also catches broken MDX and missing pages
