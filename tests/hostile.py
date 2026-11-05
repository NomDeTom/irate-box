# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One table of hostile values for every validator and writer (item 87, pattern P2). A test imports
this and tries each value; adding a value here tests it everywhere it applies."""

# An address written as something the C resolver reads as an IP address. 127.0.0.1 in every spelling,
# and the other private and link-local ranges.
NUMERIC_HOSTS = {
    "a single number": "2130706433",
    "hexadecimal parts": "0x7f.1",
    "a short dotted form": "127.1",
    "octal": "017700000001",
    "all hexadecimal": "0x7f.0x0.0x0.0x1",
    "an IPv4-mapped IPv6 literal": "[::ffff:127.0.0.1]",
    "IPv6 loopback": "[::1]",
    "full-width digits": "１２７．０．０．１",
    "a trailing dot": "127.0.0.1.",
    "link-local": "169.254.169.254",
    "link-local as a number": "2852039166",
    "private": "10.0.0.5",
}

# A web address that is wrong for a reason other than its host.
BAD_URLS = {
    "a trailing newline": "https://example.com/x\n",
    "CRLF and a header": "https://example.com/x\r\nX-Evil: 1",
    "a NUL": "https://example.com/x\x00",
    "a space": "https://example.com/a b",
    "credentials": "https://user:pass@example.com/x",
    "a user name": "https://user@example.com/x",
    "another scheme": "ftp://example.com/x",
    "javascript": "javascript:alert(1)",
    "a file": "file:///etc/passwd",
    "plain http": "http://example.com/x",
    "no host": "https:///x",
}

# Text that acts instead of showing, or ends a line, or turns the text after it around.
CONTROLS = {
    "a trailing newline": "abc\n",
    "CRLF and a header": "abc\r\nX-Evil: 1",
    "a NUL": "abc\x00def",
    "a tab": "abc\tdef",
    "ESC [ (a 7-bit CSI)": "a\x1b[31mred",
    "an 8-bit CSI": "a\x9b31mred",
    "an 8-bit OSC": "a\x9d0;title\x9cb",
    "a next-line character": "a\x85b",
    "a line separator": "a b",
    "a paragraph separator": "a b",
    "a right-to-left override": "red‮evil",
    "a bidi isolate": "a⁦b⁩c",
    "a left-to-right mark": "a‎b",
    "a lone surrogate": "a\ud800b",
}

# Text that must stay allowed: the joiners that emoji sequences and several scripts need.
JOINERS = {
    "a zero-width joiner in an emoji sequence": "\U0001f468‍\U0001f469‍\U0001f467",
    "a zero-width non-joiner in Persian": "می‌خواهم",
}

# Subdivision flags: a black flag, the tag characters that spell the region, and the cancel tag. These are
# well-formed emoji sequences, so they must stay allowed in a name or text.
FLAGS = {
    "the England flag": "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    "the Scotland flag": "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",
    "the Wales flag": "\U0001f3f4\U000e0067\U000e0062\U000e0077\U000e006c\U000e0073\U000e007f",
}

# Tag characters outside a well-formed flag: the invisible text that spells something the eye cannot see
# ("ASCII smuggling"), and the broken sequences that are not a flag.
TAG_SMUGGLING = {
    "a loose tag run": "word \U000e0067\U000e0062\U000e0065 word",
    "a tag run with no black flag": "\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    "a tag run with no cancel tag": "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067",
    "tag characters inside an otherwise ordinary word": "Eng\U000e006c\U000e0061\U000e006e\U000e0064",
}

# A name or a path that tries to leave, or to look like an option.
NAMES = {
    "dot dot": "..",
    "a path out": "../../etc/passwd",
    "an absolute path": "/etc/passwd",
    "a leading dash": "-rf",
    "an option with a value": "--output=/etc/passwd",
    "an empty value": "",
    "an over-long value": "a" * 5000,
    "a user at a host": "user@host",
    "a semicolon list": "12;34;",
    "a hash": "#x",
    "a quote and a backslash": 'a"b\\c',
    "a space": "a b",
}

# Everything for a validator that takes one value: the rows above plus the numeric hosts as bare text and as addresses.
ALL = {
    **CONTROLS,
    **NAMES,
    **{f"{why}, as a host": host for why, host in NUMERIC_HOSTS.items()},
    **{f"{why}, as an address": f"https://{host}/x" for why, host in NUMERIC_HOSTS.items()},
    **BAD_URLS,
}
