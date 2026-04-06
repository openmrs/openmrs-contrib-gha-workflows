# github-workflows
Collection of github centralised workflows that are automatically synced to other repos in the github org.


## OWASP Dependency-Check Suppressions

Known false positives for the OWASP Dependency-Check workflow are managed in [`dependency-check-suppressions.xml`](./dependency-check-suppressions.xml).

### Why individual CVE suppression?

Suppressions are intentionally scoped to **specific CVE IDs** rather than suppressing by package name or namespace. This is because many OpenMRS CVEs are incorrectly matched against OpenMRS module JARs by the scanner due to the shared `openmrs` namespace, even though the vulnerable code lives in `openmrs-core`, the Reference Application, or a different module entirely.

Suppressing by CVE ID — only after manual review — ensures we never accidentally silence a CVE that genuinely affects one of this project's real OpenMRS dependencies.

### Handling new OpenMRS CVEs

If a new CVE appears in a scan report related to OpenMRS:

1. **Check the CVE details** — identify the affected component (e.g. `openmrs-core`, a specific module, the standalone distribution) and the vulnerable version range.
2. **Determine if it is a false positive** — if the vulnerable code does not ship with this project's artifacts, it is a false positive.
3. **Add a suppression** to `dependency-check-suppressions.xml` with the CVE ID and a note explaining why it does not apply, following the existing format in that file.
4. **Do not suppress without checking** — if there is any doubt, treat the finding as legitimate until proven otherwise.

### Modifying suppressions

When updating `dependency-check-suppressions.xml`, keep the following in mind:

- Each `<suppress>` block must include a `<notes>` entry describing the CVE and the reason for suppression.
- If you are scanning an artifact that **is** the affected component (e.g. the `dataexchange` or `reportingcompatibility` module), verify the version is patched before suppressing.
- Suppressions apply to all consuming repositories that reference this workflow. Ensure a suppression is genuinely a false positive before merging.

Read more: https://dependency-check.github.io/DependencyCheck/general/suppression.html
