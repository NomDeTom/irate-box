# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2023-2026 ImranR98 and Obtainium contributors
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Which of two release tags is newer: the standard version shapes and their numeric comparison.

A port to Python of the part of Obtainium (https://github.com/ImranR98/Obtainium, GPL-3.0,
lib/services/version_service.dart at fcd1807) that orders versions by shape: `findStandardFormatsForVersion`
and `compareVersionsNumerically`. Not ported: everything that reconciles a version with the one the phone
reports as installed, which the box has no use for.

A tag has a *shape* when it fits one of 328 patterns: one to four numbers (`1`, `1.2`, `1.2.3`, `1.2.3.4`),
then optionally a pre-release word (alpha, beta, rc, pre, dev, snapshot, nightly, ose) or a number, with
a `-` or `+` before it or none, then optionally a build number. Two tags with a shape in common are compared by the numbers
of the most specific shape they share; tags with none in common cannot be compared (None), and the caller
falls back to the date.

Stdlib only.
"""

import functools
import re

_BASICS = (
    "[0-9]+",
    "[0-9]+\\.[0-9]+",
    "[0-9]+\\.[0-9]+\\.[0-9]+",
    "[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+",
)
_JOINERS = ("-", "\\+")
_WORDS = ("alpha", "beta", "rc", "pre", "dev", "snapshot", "nightly", "ose", "[0-9]+")
_BUILDS = ("\\+[0-9]+", "[0-9]+")
_DIGIT_RUN = re.compile("[0-9]+", re.ASCII)


def _standard_patterns():
    patterns = set()
    for basic in _BASICS:
        patterns.add(basic)
        for joiner in _JOINERS:
            for word in _WORDS:
                patterns.add(f"{basic}{word}")
                patterns.add(f"{basic}{joiner}{word}")
                for build in _BUILDS:
                    patterns.add(f"{basic}{word}{build}")
                    patterns.add(f"{basic}{joiner}{word}{build}")
    return sorted(patterns)


PATTERNS = _standard_patterns()
_STRICT = [(pattern, re.compile(f"^{pattern}\\Z", re.ASCII)) for pattern in PATTERNS]
_LOOSE = [(pattern, re.compile(pattern, re.ASCII)) for pattern in PATTERNS]
_CACHE = {True: {}, False: {}}
_CACHE_MAX = 4096


def formats(version, strict):
    """The set of standard patterns `version` fits: the whole string when strict, any part of it otherwise."""
    cache = _CACHE[strict]
    found = cache.get(version)
    if found is None:
        found = {pattern for pattern, compiled in (_STRICT if strict else _LOOSE) if compiled.search(version)}
        if len(cache) >= _CACHE_MAX:
            cache.clear()
        cache[version] = found
    return found


def compare(first, second):
    """Negative when `first` is older than `second`, positive when newer, 0 when the numbers are equal, None
    when the two share no standard shape and so cannot be compared this way."""
    common = formats(first, False) & formats(second, False)
    if not common:
        return None
    # The most specific shape: the most numbers, then the longest pattern.
    best = max(common, key=lambda pattern: (len(_DIGIT_RUN.findall(pattern)), len(pattern), pattern))
    compiled = re.compile(best, re.ASCII)

    def numbers(version):
        return [int(run) for run in _DIGIT_RUN.findall(compiled.search(version).group(0))]

    for mine, theirs in zip(numbers(first), numbers(second), strict=True):
        if mine != theirs:
            return 1 if mine > theirs else -1
    return 0


def newest_first(items, tag_of, date_of):
    """`items` newest first. Two tags are ordered by their numbers when they share a standard shape and the
    numbers differ, else by date (an ISO time string, a missing one oldest), else by the tag as text. Obtainium's
    default ('smartname-datefallback') in this order."""

    def older_to_newer(first, second):
        by_number = compare(tag_of(first), tag_of(second))
        if by_number:
            return by_number
        first_date, second_date = date_of(first) or "", date_of(second) or ""
        if first_date != second_date:
            return 1 if first_date > second_date else -1
        first_tag, second_tag = tag_of(first), tag_of(second)
        return (first_tag > second_tag) - (first_tag < second_tag)

    return sorted(items, key=functools.cmp_to_key(older_to_newer), reverse=True)
