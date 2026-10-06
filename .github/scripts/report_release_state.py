#!/usr/bin/env python3
"""Describe the state a failed release run left behind.

The commit and tag are pushed BEFORE publishing (npm versions are immutable, so
the reverse order could strand packages that no tag points at), and nothing can
un-publish npm. When the publish fails with nothing on npm, the workflow rolls
the tag and release commit back; otherwise the tag stays. Either way, report
exactly what did and did not happen, and what to do about it.

Configuration comes from the environment:

  RELEASE_TAG       tag this run was cutting, empty if it failed before that
  RELEASE_VERSION   version behind that tag
  BRANCH            branch the release was cut from
  PUBLISH_ONLY      "true" when this run reused an existing tag
  PUSHED            "true" when THIS run's push step succeeded; superseded by
                    the push state in STATE_FILE when that is available
  PUBLISHED         "true" when the publish step succeeded
  RELEASE_SHA       the release commit this run created
  ROLLBACK_OUTCOME  outcome of the rollback step ("success", "failure", ...)
  ROLLBACK_SHA      the revert commit the rollback pushed
  STATE_FILE        JSON written by assess_release_state.py; absent when that
                    step did not run or failed

Whether the tag was pushed comes from this run, never from the remote's tag
name alone: a tag being on the remote says nothing about who put it there, and
publish-only always reuses one that was already pushed. The assess step
refines a failed push step by checking the remote for this run's own tag
object.

Writes markdown to GITHUB_STEP_SUMMARY. This runs when the job has already
failed, so it must never add a failure of its own: every path exits 0.
"""

import json
import os
import sys

HEADING = "### ❌ Release failed — current state"
NO_GH_RELEASE = (
    "No GitHub Release was created: that job is skipped when this one fails."
)


def render(tag, version, branch, publish_only, pushed, state=None, published=False,
           rollback="", rollback_sha="", release_sha=""):
    """Return the markdown report for the state this run ended in.

    Pure: `state` is the assess step's {"live", "missing", "unknown"} package
    lists, or None when npm's state could not be established, so the branching
    can be tested without touching the network. `pushed` is None when it could
    not be established whether the tag reached the remote.
    """
    out = ["", HEADING, ""]

    if not tag:
        out.append(
            "Failed before a release tag was computed. Nothing was committed, "
            "pushed or published — fix the error above and re-run."
        )
        return "\n".join(out) + "\n"

    if published:
        out += [
            f"Every package was published at `{version}` and tag `{tag}` is "
            "live; the failure came afterwards. Do not delete the tag.",
            "",
            NO_GH_RELEASE,
        ]
        return "\n".join(out) + "\n"

    if not publish_only and pushed is None:
        commit = f"`{release_sha}`" if release_sha else "this run's release commit"
        out += [
            f"Could not tell whether this run's tag `{tag}` reached the remote, "
            "or the remote has a tag of that name this run did not create. "
            "Nothing was published.",
            "",
            f"Check what the remote's `{tag}` points at before re-running. Only "
            f"if it is {commit}, re-run with **publish_only** and "
            f"`release_version: {version}`. If there is no such tag, fix the "
            "error above and re-run. If it points elsewhere, do not use "
            "publish_only: it would publish that other commit.",
        ]
        return "\n".join(out) + "\n"

    if not publish_only and not pushed:
        out += [
            f"- **Tag `{tag}`:** not pushed",
            "- **npm:** nothing published",
            "",
            f"Nothing left the runner, so `{version}` is still free. Fix the "
            "error above and re-run.",
        ]
        return "\n".join(out) + "\n"

    if rollback == "success":
        out += [
            f"Publishing failed and npm had none of the packages at `{version}`, "
            f"so the release was rolled back: tag `{tag}` was deleted and the "
            f"release commit was reverted in `{rollback_sha}`. `{version}` is "
            "still free. Fix the error above and re-run the release.",
            "",
            NO_GH_RELEASE,
        ]
        return "\n".join(out) + "\n"

    # The tag is live: this run pushed it and could not roll it back, or
    # publish-only reused an existing one.
    if publish_only:
        out.append(
            f"- **Tag `{tag}`:** pre-existing — this run committed and pushed nothing"
        )
    else:
        out.append(
            f"- **Tag `{tag}`:** pushed to `{branch}` — commit and tag are live"
        )

    live = state["live"] if state else []
    missing = state["missing"] if state else []
    unknown = state["unknown"] if state else []
    # With no packages classified at all, nothing is known about npm.
    known = bool(live or missing or unknown)

    if live:
        out.append(f"- **npm — already published at `{version}`:**")
        out += [f"  - `{name}`" for name in live]
    if unknown:
        out.append("- **npm, could not check:**")
        out += [f"  - `{name}`" for name in unknown]
    if not known:
        out.append(f"- **npm:** could not determine what was published at `{version}`")
    elif not live and not unknown:
        out.append(f"- **npm:** nothing published at `{version}`")
    # Only worth naming when the publish got partway; if nothing published, the
    # line above already says so.
    if missing and (live or unknown):
        out.append("- **npm — NOT published:**")
        out += [f"  - `{name}`" for name in missing]

    out += ["", "#### Recovery", ""]

    if live or unknown or not known:
        out += [
            f"⚠️ Some packages are or may be on npm at `{version}`, and "
            "**npm versions cannot be republished or reused**. Do not re-run "
            f"this workflow at `{version}`, and do not delete the tag — it "
            "may match what is on npm.",
            "",
            f"Fix the error above and re-run with **publish_only** and "
            f"`release_version: {version}`. That only finishes the release if "
            "the publish command passes `--tolerate-republish`; otherwise "
            f"publish the missing packages by hand at `{version}`, or leave "
            f"`{version}` partial and cut the next version.",
        ]
    elif publish_only:
        out.append(
            "Nothing reached npm and the tag is untouched. Fix the error above "
            f"and re-run with **publish_only** and `release_version: {version}`."
        )
    else:
        revert_ref = release_sha or f"{tag}^{{commit}}"
        out += [
            "Nothing reached npm, but the automatic rollback failed (see its "
            "log), so the tag and commit are still live. Pick one:",
            "",
            "1. **Finish this release** — re-run with **publish_only** and "
            f"`release_version: {version}`. It builds and publishes the existing "
            "tag; no second bump or commit.",
            "2. **Start over** — remove the tag and the release commit together, "
            "then release again:",
            "",
            "```bash",
            f"git revert --no-edit {revert_ref}",
            f"git push --atomic origin HEAD:refs/heads/{branch} :refs/tags/{tag}",
            "```",
        ]

    out += ["", NO_GH_RELEASE]
    return "\n".join(out) + "\n"


def load_state(path):
    """Return the assess step's package classification, or None if unavailable."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        print(f"::warning::Could not read release state {path}: {e}", file=sys.stderr)
        return None


def write_summary(markdown):
    """Append to GITHUB_STEP_SUMMARY, or stdout when running outside Actions."""
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_file:
        print(markdown, end="")
        return
    with open(summary_file, "a") as f:
        f.write(markdown)


PUSHED_BY_STATE = {"pushed": True, "not_pushed": False, "unknown": None, "preexisting": False}


def main():
    env = os.environ.get
    state = load_state(env("STATE_FILE", ""))
    pushed = env("PUSHED", "") == "true"
    if state and state.get("push") in PUSHED_BY_STATE:
        pushed = PUSHED_BY_STATE[state["push"]]
    write_summary(render(
        tag=env("RELEASE_TAG", "").strip(),
        version=env("RELEASE_VERSION", "").strip(),
        branch=env("BRANCH", "").strip(),
        publish_only=env("PUBLISH_ONLY", "") == "true",
        pushed=pushed,
        state=state,
        published=env("PUBLISHED", "") == "true",
        rollback=env("ROLLBACK_OUTCOME", ""),
        rollback_sha=env("ROLLBACK_SHA", ""),
        release_sha=env("RELEASE_SHA", ""),
    ))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - the job has already failed
        # Never stack a second failure on top of the one being reported.
        print(f"::warning::Could not report release state: {e}", file=sys.stderr)
    sys.exit(0)
