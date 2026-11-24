#!/usr/bin/env python3
"""Fill in the time placeholders in close-stale-prs.yml's comment templates.

Reads DAYS_BEFORE_STALE, DAYS_BEFORE_CLOSE, STALE_PR_MESSAGE and CLOSE_PR_MESSAGE
from the environment and writes the rendered messages as the `stale-pr-message`
and `close-pr-message` outputs. Text in braces that is not a known placeholder
is left as is.
"""

import math
import os

from utils import write_github_outputs

_SECOND = 1
_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR
# Average Gregorian month and year, since there is no calendar date to measure from.
_MONTH = 30.436875 * _DAY
_YEAR = 365.2425 * _DAY

# dayjs relativeTime's default thresholds and English strings: (format, limit, unit).
# A row with a unit re-measures the duration in that unit; a row's limit applies to
# the latest measurement.
_THRESHOLDS = [
    ("a few seconds", 44, _SECOND),
    ("a minute", 89, None),
    ("%d minutes", 44, _MINUTE),
    ("an hour", 89, None),
    ("%d hours", 21, _HOUR),
    ("a day", 35, None),
    ("%d days", 25, _DAY),
    ("a month", 45, None),
    ("%d months", 10, _MONTH),
    ("a year", 17, None),
    ("%d years", None, _YEAR),
]


def humanize_days(days):
    """Describe a number of days the way dayjs's `fromNow(true)` does, e.g. 150 -> "5 months"."""
    seconds = abs(days) * _DAY
    amount = 0
    for i, (fmt, limit, unit) in enumerate(_THRESHOLDS):
        if unit is not None:
            # JavaScript's Math.round, which rounds halves up.
            amount = math.floor(seconds / unit + 0.5)
        if limit is None or amount <= limit:
            if amount <= 1 and i > 0:
                fmt = _THRESHOLDS[i - 1][0]
            return fmt.replace("%d", str(amount))
    raise AssertionError("unreachable: the last threshold has no limit")


def render(template, days_before_stale, days_before_close):
    """Replace the supported placeholders in `template`."""
    values = {
        "{time-before-stale}": humanize_days(days_before_stale),
        "{time-before-close}": humanize_days(days_before_close),
        "{inactive-time-at-close}": humanize_days(days_before_stale + days_before_close),
    }
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    return template


def main():
    stale = float(os.environ["DAYS_BEFORE_STALE"])
    close = float(os.environ["DAYS_BEFORE_CLOSE"])
    write_github_outputs(
        {
            "stale-pr-message": render(os.environ["STALE_PR_MESSAGE"], stale, close),
            "close-pr-message": render(os.environ["CLOSE_PR_MESSAGE"], stale, close),
        }
    )


if __name__ == "__main__":
    main()
