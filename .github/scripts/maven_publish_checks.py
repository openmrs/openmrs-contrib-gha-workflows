#!/usr/bin/env python3
"""Pre-flight checks for publishing Maven artifacts outside the openmrs org.

`target` decides whether this repo may deploy to TARGET_URL. Repos outside
the openmrs GitHub org may not deploy to the repositories OpenMRS itself
publishes to, nor publish `org.openmrs` groupIds to Maven Central. These are
the targets a fork inherits from the OpenMRS parent POM, so the check keeps
such forks from deploying where they cannot (or should not) publish, while
leaving other repositories on the same hosts (e.g. PIH's `modules-pih`)
alone. Configuration:

  REPOSITORY_OWNER  owner of the repo running the workflow
  TARGET_URL        repository URL(s) the build would deploy to, separated by
                    whitespace; may be empty
  GROUP_IDS         JSON array of the build's module groupIds
  CENTRAL_PUBLISHING  "true" when the build uses central-publishing-maven-plugin,
                    which publishes to Maven Central without a repository URL

Writes `allowed=true|false` and, when not allowed, `reason=<text>`.

`scm` takes SCM_CONNECTIONS, a JSON array of the POM's scm developerConnection
and connection. maven-release-plugin pushes to and checks out from those URLs
rather than the checked-out remote, and many POMs still name a repo's old or
upstream location, so the release redirects them to GITHUB_REPOSITORY. Fails
when the POM has no scm connection at all; otherwise writes `urls=<url> ...`,
the git URLs to redirect.
"""

import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import write_github_outputs

OPENMRS_OWNER = "openmrs"
OPENMRS_REPO_HOSTS = {"mavenrepo.openmrs.org"}
OPENMRS_ARTIFACTORY_HOST = "openmrs.jfrog.io"
OPENMRS_ARTIFACTORY_REPOS = {"modules", "snapshots", "releases"}
# Hosts (and their subdomains) that publish to Maven Central, including the
# OSSRH staging API and Central Portal.
MAVEN_CENTRAL_DOMAINS = ("maven.org", "maven.apache.org", "sonatype.com", "sonatype.org")

SCM_GITHUB_RE = re.compile(
    r"github\.com[:/]+(?P<owner>[^/:]+)/(?P<repo>[^/]+?)(?:\.git)?/*$", re.IGNORECASE
)


def is_openmrs_group(group_id):
    return group_id == "org.openmrs" or group_id.startswith("org.openmrs.")


def is_maven_central(host):
    return any(host == d or host.endswith(f".{d}") for d in MAVEN_CENTRAL_DOMAINS)


def central_reason(group_ids):
    reserved = sorted(g for g in group_ids if is_openmrs_group(g))
    if reserved:
        return f"groupId {', '.join(reserved)} on Maven Central belongs to OpenMRS"
    return None


def openmrs_owned_reason(url, group_ids):
    """Return why `url` is reserved for the openmrs org, or None if it is not."""
    parsed = urllib.parse.urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    segments = [s for s in parsed.path.split("/") if s]
    if host in OPENMRS_REPO_HOSTS:
        return f"{url} is an OpenMRS repository"
    if (
        host == OPENMRS_ARTIFACTORY_HOST
        and len(segments) >= 2
        and segments[0] == "artifactory"
        and segments[1] in OPENMRS_ARTIFACTORY_REPOS
    ):
        return f"{url} is an OpenMRS repository"
    if is_maven_central(host):
        return central_reason(group_ids)
    return None


def scm_repository(connection):
    """Return 'owner/repo' (lowercased) for a GitHub SCM connection, or None."""
    value = connection.strip()
    if value.lower().startswith("scm:git:"):
        value = value[len("scm:git:"):]
    match = SCM_GITHUB_RE.search(value)
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}".lower()


def check_target(env):
    owner = env.get("REPOSITORY_OWNER", "").strip().lower()
    urls = env.get("TARGET_URL", "").split()
    group_ids = json.loads(env.get("GROUP_IDS") or "[]")
    reason = None
    if owner != OPENMRS_OWNER:
        reason = next(filter(None, (openmrs_owned_reason(u, group_ids) for u in urls)), None)
        if not reason and env.get("CENTRAL_PUBLISHING") == "true":
            reason = central_reason(group_ids)
    write_github_outputs({"allowed": str(reason is None).lower(), "reason": reason})


def git_url(connection):
    """The URL git sees for an `scm:git:` connection.

    maven-scm hands git the URL exactly as written, so the redirect matches that
    spelling rather than a shorter prefix that would also catch sibling repos.
    """
    value = connection.strip()
    return value[len("scm:git:"):] if value.lower().startswith("scm:git:") else value


def check_scm(env):
    connections = [c for c in json.loads(env.get("SCM_CONNECTIONS") or "[]") if c and c.strip()]
    repository = env.get("GITHUB_REPOSITORY", "").strip()
    if not connections:
        sys.exit(
            "::error::The POM has no <scm><developerConnection>; maven-release-plugin needs it to "
            f"push the release. Set it to scm:git:https://github.com/{repository}.git"
        )
    for connection in connections:
        if scm_repository(connection) != repository.lower():
            print(
                f"::notice::The POM's scm points at '{connection}'; the release pushes to "
                f"{repository} instead. Consider updating <scm> in the POM."
            )
    urls = sorted({git_url(c) for c in connections})
    write_github_outputs({"urls": " ".join(urls)})


def main(argv):
    checks = {"target": check_target, "scm": check_scm}
    if len(argv) != 2 or argv[1] not in checks:
        sys.exit(f"usage: {os.path.basename(argv[0])} target|scm")
    checks[argv[1]](os.environ)


if __name__ == "__main__":
    main(sys.argv)
