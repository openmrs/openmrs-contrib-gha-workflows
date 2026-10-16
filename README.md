# openmrs-contrib-gha-workflows

Reusable GitHub Actions workflows for building, testing and releasing OpenMRS frontend (O3) and backend (Maven)
modules, for repositories in the OpenMRS organization and beyond.

**Documentation: https://openmrs.github.io/openmrs-contrib-gha-workflows/**

The documentation covers getting started inside and outside the OpenMRS organization, and each workflow's inputs,
secrets and permissions. Its source is in [`docs/`](docs).

## Working on this repository

- Python scripts used by the workflows live in [`.github/scripts`](.github/scripts), with tests in
  [`.github/tests`](.github/tests): `python3 -m unittest discover -s .github/tests`.
- The documentation site is a [Nextra](https://nextra.site) site. Run `pnpm install` and `pnpm dev` in `docs/`. The
  input, secret and output tables are generated from the workflow YAML on every build, so change descriptions in the
  workflows rather than in the pages. Pull requests from branches in this repository get a preview link in a comment.

## Dependency updates and smoke testing

[Dependabot](.github/dependabot.yml) opens a grouped, weekly pull request that bumps the pinned action SHAs across both
the reusable workflows (`.github/workflows`) and the composite actions (`.github/actions`). A separate weekly pull
request bumps `.github/tools`, the npm packages (such as `semver`) the workflows install for their own use.

Because the actions here only ever run when consuming repositories invoke them, a regression in a bumped action (for
example a new major `actions/checkout`) would otherwise surface downstream rather than in this repo. The
[smoke-test workflow](.github/workflows/smoke-test.yml) guards against that: on every pull request it runs the inference
scripts' unit tests and lint, then drives the shared `maven-build` / `infer-backend-params` / `infer-frontend-params`
actions against tiny throwaway projects under [`.github/tests/fixtures`](.github/tests/fixtures), and runs
`capture-docker-logs` against throwaway containers. Reusing the same composite actions the production workflows call
keeps the smoke test from drifting away from real consumer behaviour.

This covers the build-path actions: `checkout`, `setup-java`, `setup-node`, `cache`, and `upload-artifact`. Actions
that require org secrets or external services (the SNAPSHOT/release deploys, Transifex sync, the GitHub App token, and
the Codecov upload itself) are **not** smoke-tested and should be reviewed manually when their pins change. The
coverage shell logic (`stage-coverage.sh` and `prepare-codecov-upload.sh`) is covered by its own unit tests
(`test_stage_coverage.py`, `test_prepare_codecov_upload.py`) instead.
