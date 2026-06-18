# github-workflows

Collection of github centralised workflows that are automatically synced to other repos in the github org.

## Bot authentication

Workflows that need elevated, cross-repo, or branch-protection-bypassing access authenticate as a **GitHub App**,
minting a short-lived installation token at runtime via
[`actions/create-github-app-token`](https://github.com/actions/create-github-app-token). Each workflow exposes two
optional `workflow_call` secrets — `APP_ID` and `APP_PRIVATE_KEY` — that the caller wires to the relevant App.

To avoid breaking repositories that have not yet been migrated, each workflow resolves its token with a fallback chain:

```
App token (if APP_ID + APP_PRIVATE_KEY are set)  →  legacy bot PAT (if set)  →  github.token
```

`github.token` is only a viable last resort for the **module release** checkout (a repo-local operation). The other
three operations cannot work under `github.token` — cross-repo dispatch/push for the distro and dashboard workflows, and
a CI-triggering, self-approvable PR for translations — so those workflows omit it and **fail fast** with a clear error
when neither App credentials nor the legacy PAT are supplied.

So a repo that passes the App credentials uses the App; a repo still passing the old PAT keeps working unchanged. Once
every consuming repo has migrated, the legacy PAT secrets and the `|| secrets.<PAT>` fallback can be removed.

The four functions are backed by four separate Apps, each installed only where it is needed and granted the minimum
permissions:

| Function | Workflow | App permissions | Installed on |
| --- | --- | --- | --- |
| Translation updates | `tx-pull.yml` | `contents: write`, `pull-requests: write` | repos with Transifex automation |
| Module release | `release-backend-module.yml` | `contents: write` (+ ruleset bypass) | released backend module repos |
| Distro build trigger | `release-frontend-module.yml` | `actions: write` | `openmrs-distro-referenceapplication` |
| Security dashboard | `owasp-dependency-check.yml` | `contents: write` | `openmrs-contrib-dependency-vulnerability-dashboard` |

Recommended org-secret names for the App credentials: `OMRS_TRANSLATION`, `OMRS_MODULE_RELEASE`, `OMRS_ESM_RELEASE`, and
`OMRS_SEC_DASHBOARD` (each with an `_APP_ID` / `_APP_PRIVATE_KEY` pair). A caller wires them to the generic inputs, e.g.:

```yaml
jobs:
  pull-translations:
    uses: openmrs/openmrs-contrib-gha-workflows/.github/workflows/tx-pull.yml@main
    secrets:
      TRANSIFEX_TOKEN: ${{ secrets.TRANSIFEX_TOKEN }}
      APP_ID:          ${{ secrets.OMRS_TRANSLATION_APP_ID }}
      APP_PRIVATE_KEY: ${{ secrets.OMRS_TRANSLATION_APP_PRIVATE_KEY }}
```

### Setup notes

- **Branch-protection bypass:** unlike an admin PAT, a GitHub App token does **not** bypass branch protection / rulesets
  implicitly. The module release App must be added to each target repo's ruleset **bypass list**.
- **App credentials are all-or-nothing:** supply both `APP_ID` and `APP_PRIVATE_KEY` or neither. Supplying only one (e.g.
  a typo in a secret name) fails the run, rather than silently falling back to the PAT.
- **Cross-repo scope:** the distro and dashboard tokens are minted scoped to the target repo (`owner` + `repositories`),
  so those Apps must be installed on the target repo even when the workflow runs elsewhere.
- **`github.token` is a repo-local safety net only:** it cannot bypass branch protection or act across repositories. The
  module release checkout falls back to it (a push only fails later if the branch is protected), but the distro-dispatch,
  dashboard-sync, and translation workflows omit it from the fallback and **fail fast** with a clear error when neither
  App credentials nor the legacy PAT are provided.
- **Dashboard sync is org-scoped:** `owasp-dependency-check` only syncs the report to the dashboard repo on
  `push`/`workflow_dispatch` events in the `openmrs` org, so forks run the scan without needing dashboard credentials.
- **Token lifetime:** App installation tokens expire after one hour. A backend release pushes its commit/tag during
  `release:prepare` — well before the longer `release:perform` deploy — so the token is normally used long before it
  expires. For an unusually long pre-push build, use the legacy PAT, which does not expire.

## OWASP Dependency-Check

There is a reusable workflow that runs [OWASP Dependency-Check](https://dependency-check.github.io/DependencyCheck/) against Java
and Node.js projects. It auto-detects project type, builds the project, scans dependencies against the NVD, and uploads
the report as an artifact. On push/dispatch events it also syncs the JSON report to
the [vulnerability dashboard repo](https://github.com/openmrs/openmrs-contrib-dependency-vulnerability-dashboard). The
CVSS failure threshold defaults to `6.2` and an NVD API key can be provided via the `NVD_API_KEY` secret for faster
database updates.

### Suppressions

Known false positives are managed in [`dependency-check-suppressions.xml`](./.github/resources/owasp/dependency-check-suppressions.xml).

#### Why individual CVE suppression?

For **OpenMRS CVEs**, suppressions must be scoped to **specific CVE IDs** rather than by package name or namespace. Many
OpenMRS CVEs are incorrectly matched against OpenMRS module JARs by the scanner due to the shared `openmrs` namespace,
even though the vulnerable code lives in `openmrs-core`, the Reference Application, or a different module entirely.
Suppressing by CVE ID — only after manual review — ensures we never accidentally silence a CVE that genuinely affects
one of this project's real OpenMRS dependencies.

For **non-OpenMRS packages**, broader suppression scopes (by package name, namespace, etc.) are acceptable where
appropriate.

#### Handling new OpenMRS CVEs

If a new CVE appears in a scan report related to OpenMRS:

1. **Check the CVE details** — identify the affected component (e.g. `openmrs-core`, a specific module, the standalone
   distribution) and the vulnerable version range.
2. **Determine if it is a false positive** — if the vulnerable code does not ship with this project's artifacts, it is a
   false positive.
3. **Add a suppression** to `.github/resources/owasp/dependency-check-suppressions.xml` with the CVE ID and a note explaining why it does not
   apply, following the existing format in that file.
4. **Do not suppress without checking** — if there is any doubt, treat the finding as legitimate until proven otherwise.

#### Modifying suppressions

When updating `.github/resources/owasp/dependency-check-suppressions.xml`, keep the following in mind:

- Each `<suppress>` block must include a `<notes>` entry describing the CVE and the reason for suppression.
- If you are scanning an artifact that **is** the affected component (e.g. the `dataexchange` or
  `reportingcompatibility` module), verify the version is patched before suppressing.
- Suppressions apply to all consuming repositories that reference this workflow. Ensure a suppression is genuinely a
  false positive before merging.

Read more: https://dependency-check.github.io/DependencyCheck/general/suppression.html
