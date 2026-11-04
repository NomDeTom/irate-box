# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One place for the checks and the writers that keep an outside value inside its format.

Every value irate-box takes from outside (a page, a request file, the hub's state, a manifest,
a stick, the internet) is checked here before it is used, and written by the writer for the
format it lands in:

  names       ssid, hostname, iface, name, account, package, version, repo, branch, sha256, card,
              country, unit, ip
  paths       path() under a named root: no "..", no symlink out, no special file
  URLs        url(): an allowed scheme and, where given, an allowed host; no credentials, no IP
              literals (so no link-local), no whitespace
  values      text(), choice(), number(), argv()
  writers     hostapd_ssid() (ssid2= hex), keyfile_ssid() (NetworkManager byte list),
              keyfile_value() (GLib key-file escaping), config_line(), html_text()

The hub and root both import this module, so they agree. Root still runs every check again on
anything that came from the hub's side: a value checked by the hub is not trusted by root.

Stdlib only.
"""

import html
import ipaddress
import os
import re
import stat
from pathlib import Path
from urllib.parse import quote, urlsplit

CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,63}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9.+~:-]{1,100}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CARD_RE = re.compile(r"^sd[a-z]{1,2}$")
FS_UUID_RE = re.compile(r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9.-]{0,63}$")
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.img(?:\.xz)?$")
UNIT_RE = re.compile(r"^[A-Za-z0-9@_][A-Za-z0-9@_.-]*\.service$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,100}$")


class Invalid(ValueError):
    """A value that is not safe for the place it was going to land."""


def _need(value, what):
    if not isinstance(value, str):
        raise Invalid(f"{what} must be text.")
    return value


def _short(value, what, most):
    value = _need(value, what)
    if len(value) > most:
        raise Invalid(f"{what} is too long.")
    return value


def ssid(value):
    """A WiFi network name: 1 to 32 bytes of UTF-8, no control characters. Any other character
    is allowed; the writers decide how to put it in hostapd or a NetworkManager keyfile."""
    value = _need(value, "The WiFi network name")
    if not value or len(value.encode("utf-8")) > 32:
        raise Invalid("The WiFi network name must be 1 to 32 bytes.")
    if CONTROL_RE.search(value):
        raise Invalid("The WiFi network name cannot contain control characters.")
    return value


def hostname(value):
    value = _need(value, "The box name")
    if not HOSTNAME_RE.fullmatch(value):
        raise Invalid(
            "The box name must be 1 to 63 lowercase letters, digits and hyphens, not starting or ending with a hyphen."
        )
    return value


def iface(value):
    value = _need(value, "The interface name")
    if not IFACE_RE.fullmatch(value):
        raise Invalid("The interface name is not valid.")
    return value


def name(value, what="The name", most=64):
    value = _need(value, what)
    if len(value) > most or not NAME_RE.fullmatch(value):
        raise Invalid(f"{what} may use letters, digits, '.', '_' and '-', starting with a letter or digit.")
    return value


def account(value):
    value = _need(value, "The account name")
    if not ACCOUNT_RE.fullmatch(value):
        raise Invalid("An account name has 3 to 32 letters, digits, '.', '_' or '-'.")
    return value


def package(value):
    value = _need(value, "The package name")
    if not PACKAGE_RE.fullmatch(value):
        raise Invalid("The package name is not valid.")
    return value


def version(value):
    value = _need(value, "The version")
    if not VERSION_RE.fullmatch(value):
        raise Invalid("The version is not valid.")
    return value


def repo(value):
    value = _need(value, "The repository")
    if not REPO_RE.fullmatch(value):
        raise Invalid("Enter the repository as account/repository.")
    return value


def branch(value):
    value = _need(value, "The branch")
    if (
        not BRANCH_RE.fullmatch(value)
        or value.startswith(("-", "/", "."))
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "//" in value
    ):
        raise Invalid("The branch name is not valid.")
    return value


def sha256(value):
    value = _need(value, "The checksum").lower()
    if not SHA256_RE.fullmatch(value):
        raise Invalid("The checksum must be 64 hexadecimal digits.")
    return value


def card(value):
    value = _need(value, "The card")
    if not CARD_RE.fullmatch(value):
        raise Invalid("The card name is not valid.")
    return value


def image(value):
    """An image name for a board's card: .img or .img.xz, with no path."""
    value = _need(value, "The image name")
    if not IMAGE_RE.fullmatch(value):
        raise Invalid("The image name is not valid.")
    return value


def loader(value):
    """A loader's id, as the board list names it (a chip's short lower-case name)."""
    value = _need(value, "The loader")
    if not re.fullmatch(r"[a-z0-9]{1,16}", value):
        raise Invalid("The loader name is not valid.")
    return value


def country(value, allowed=None):
    value = _need(value, "The country")
    if not COUNTRY_RE.fullmatch(value):
        raise Invalid("The country must be a two-letter code.")
    if allowed is not None and value not in allowed:
        raise Invalid("That country is not in the box's list.")
    return value


def unit(value):
    value = _need(value, "The service")
    if not UNIT_RE.fullmatch(value) or value.startswith("-"):
        raise Invalid("The service name is not valid.")
    return value


def fs_uuid(value):
    """A filesystem UUID as lsblk gives it, safe in a systemd mount unit and a /dev/disk link."""
    value = _need(value, "The filesystem UUID")
    if not FS_UUID_RE.fullmatch(value):
        raise Invalid("The filesystem UUID is not valid.")
    return value


def token(value, what="The session token", low=20, high=100):
    """A session cookie's shape: the URL-safe letters, digits, '-' and '_' that token_urlsafe makes,
    with a length before anything hashes or looks it up."""
    value = _need(value, what)
    if not low <= len(value) <= high or not TOKEN_RE.fullmatch(value):
        raise Invalid(f"{what} is not valid.")
    return value


def ip(value):
    value = _need(value, "The address")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise Invalid("The address is not valid.") from None


def text(value, what="The text", most=4096, multiline=False):
    """Text for a message, a record or a page. No control characters unless newlines are allowed,
    and never longer than `most` characters."""
    value = _need(value, what)
    if len(value) > most:
        raise Invalid(f"{what} is too long.")
    bad = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]") if multiline else CONTROL_RE
    if bad.search(value):
        raise Invalid(f"{what} cannot contain control characters.")
    return value


def choice(value, allowed, what="The value"):
    if value not in allowed:
        raise Invalid(f"{what} is not one of the allowed values.")
    return value


def number(value, low, high, what="The number"):
    if type(value) is not int or not low <= value <= high:
        raise Invalid(f"{what} must be a whole number from {low} to {high}.")
    return value


def argv(command, what="The command"):
    """A command as an argument list, never a string. Every argument is text without a NUL, CR or
    LF, so nothing can add a line to a script that reads it."""
    if not isinstance(command, (list, tuple)) or not command:
        raise Invalid(f"{what} must be given as a list of arguments.")
    out = []
    for part in command:
        part = _need(part, f"{what} argument")
        if any(c in part for c in "\x00\r\n"):
            raise Invalid(f"{what} cannot contain a NUL, CR or LF.")
        out.append(part)
    return out


def path(value, root, what="The path", must_exist=False, allow_symlink=False):
    """`value` inside `root`: normalised and resolved, never through a symlink that leaves the
    root. A missing leaf is checked through its existing parent."""
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    value = _need(value, what)
    if not value:
        raise Invalid(f"{what} is empty.")
    if "\x00" in value:
        raise Invalid(f"{what} cannot contain a NUL.")
    root_real = os.path.realpath(os.fspath(root))
    full = value if os.path.isabs(value) else os.path.join(root_real, value)
    full = os.path.normpath(full)
    real = os.path.realpath(full)
    if real != root_real and not real.startswith(root_real + os.sep):
        raise Invalid(f"{what} is outside the folder it belongs in.")
    if must_exist and not os.path.exists(real):
        raise Invalid(f"{what} does not exist.")
    try:
        if not allow_symlink and os.path.islink(full):
            raise Invalid(f"{what} is a symbolic link.")
        st = os.lstat(real)
    except FileNotFoundError:
        st = None
    except OSError:
        st = None
    if st is not None and not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
        raise Invalid(f"{what} is not a plain file or folder.")
    return Path(full)


def url(value, schemes=("https",), hosts=None, what="The address", allow_ip=False):
    """A web address with an allowed scheme and, when `hosts` is given, an allowed host. No
    credentials, no IP literal (so no link-local), no whitespace or control characters."""
    value = _need(value, what)
    if CONTROL_RE.search(value) or any(c.isspace() for c in value):
        raise Invalid(f"{what} cannot contain spaces or control characters.")
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme not in schemes:
        raise Invalid(f"{what} must start with " + " or ".join(f"{s}://" for s in schemes) + ".")
    host = (parts.hostname or "").lower()
    if not host:
        raise Invalid(f"{what} has no host.")
    if parts.username is not None or parts.password is not None:
        raise Invalid(f"{what} cannot contain a user name or password.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not allow_ip:
            raise Invalid(f"{what} must name a host, not an IP address.")
    if hosts is not None and not any(host == h.lower() or host.endswith("." + h.lower()) for h in hosts):
        raise Invalid(f"{what} is not on an allowed host.")
    return value


def public_url(value, what="The address", resolve=None):
    """An address from an answer or a redirect: https, a host name (never an IP literal), and one
    whose addresses are all public, so a project's answer cannot send the box to its own network."""
    import socket

    value = url(value, schemes=("https",), what=what)
    host = urlsplit(value).hostname or ""
    try:
        infos = (resolve or socket.getaddrinfo)(host, None)
    except OSError:
        raise Invalid(f"{what} does not resolve.") from None
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except (IndexError, TypeError, ValueError):
            continue
        if not address.is_global or address.is_multicast:
            raise Invalid(f"{what} resolves to an address inside the box's own networks.")
    return value


def hostapd_ssid(value):
    """hostapd's own safe spelling of any name: ssid2=<hex bytes>."""
    return "ssid2=" + ssid(value).encode("utf-8").hex()


def keyfile_ssid(value):
    """A NetworkManager keyfile's SSID line: a byte list, so ';', '#', quotes and any UTF-8 name
    are read as the name itself, never as a list or a second line."""
    raw = ssid(value).encode("utf-8")
    return "ssid=" + ";".join(str(byte) for byte in raw) + ";"


def keyfile_value(value):
    """A value as GLib key files read it: backslash, CR, LF, tab and leading spaces escaped."""
    value = _need(value, "The key file value")
    if "\x00" in value:
        raise Invalid("A key file value cannot contain a NUL.")
    out, leading = [], True
    for char in value:
        if char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif char == " " and leading:
            out.append("\\s")
            continue
        else:
            out.append(char)
        leading = False
    return "".join(out)


def config_line(key, value, what="The setting"):
    """One key=value line for a hostapd, dnsmasq or NetworkManager-style file: a checked key and a
    value that cannot end the line."""
    key = _need(key, "The setting name")
    if not KEY_RE.match(key):
        raise Invalid("The setting name is not valid.")
    value = _need(value, what)
    if any(char in value for char in "\x00\r\n"):
        raise Invalid(f"{what} cannot contain a NUL, CR or LF.")
    return f"{key}={value}"


def html_text(value):
    """Text for an HTML page: escaped, never markup."""
    return html.escape(str(value), quote=True)


def content_disposition(filename, disposition="attachment"):
    """A Content-Disposition value for a download: no CR, LF or NUL; a quoted ASCII name with
    quotes and backslashes replaced; and RFC 6266's filename* for the real, UTF-8 name."""
    name = _need(filename, "The file name")
    if any(char in name for char in "\x00\r\n"):
        raise Invalid("The file name cannot contain a NUL, CR or LF.")
    name = name.rsplit("/", 1)[-1]
    if not name:
        raise Invalid("The file name is empty.")
    fallback = "".join(char if 32 <= ord(char) < 127 and char not in '"\\' else "_" for char in name)
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
