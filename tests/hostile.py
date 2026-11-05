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
