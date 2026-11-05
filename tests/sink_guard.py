# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Every outside value reaches its sink through a shared writer (item 87, pattern P3). Read with ast;
nothing imported from irate_box. Under irate_box/**/*.py, fail on

  * `html.escape` (or `from html import escape`) outside validate.py: `validate.html_text`;
  * a `send_header(` whose value is not a constant, `validate.header_value(...)`,
    `validate.content_disposition(...)`, the drop's `_drop_disposition(...)` or `str(<number>)`, and not
    listed in SEND_HEADER_ALLOWED with the reason it cannot hold a line break;
  * in the modules that write another program's configuration, an f-string `key=<value>` whose value
    is not a `validate.*` call, a keyfile or hostapd writer, an `int(...)`, a module constant, and
    not listed in CONF_ALLOWED with its reason (a new line of configuration goes through
    `validate.config_line`);
  * `validate.html_text(...)` in an attribute that is not inside double quotes, or in one that holds
    an address (href, src, action, ...) without being listed in URL_ATTRIBUTES_ALLOWED with the check
    that makes the address safe: `javascript:` survives escaping.

`subprocess` with `shell=True` or a string command is tests/subprocess_guard.py. A listed entry is
(file, source of the value) -> reason. python3 tests/sink_guard.py"""

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SEND_HEADER_ALLOWED = {
    ("irate_box/hub/appstore.py", "kind"): "the file's type from a fixed table (_type_of)",
    ("irate_box/hub/appstore.py", "f'\"{note[2]}\"'"): "the file's sha256, recorded by the keeper from a digest, hex",
    ("irate_box/hub/appstore.py", "f'bytes {start}-{end}/{size}'"): "integers",
    ("irate_box/hub/appstore.py", "f'bytes */{size}'"): "an integer",
    ("irate_box/hub/server.py", "len(body)"): "an integer",
    (
        "irate_box/hub/server.py",
        "f'attachment; filename=\"irate-box-{kind}-{stamp}.tar.gz\"'",
    ): "kind and stamp are the hub's own words and a clock reading",
    ("irate_box/hub/server.py", "MIME.get(file_path.suffix, 'application/octet-stream')"): "a fixed table",
    (
        "irate_box/hub/server.py",
        "v",
    ): "the headers of a reply made here from constants and numbers (send_empty and the static answers)",
    ("irate_box/hub/server.py", "kind"): "the content type chosen by the code that read the file, from a fixed table",
    ("irate_box/hub/server.py", "MENU_REDIRECTS[path]"): "a fixed table of paths",
    ("irate_box/hub/server.py", "MIME['.html']"): "a fixed table",
    ("irate_box/hub/server.py", "f'bytes {start}-{end}/{size}'"): "integers",
    ("irate_box/hub/server.py", "f'bytes */{size}'"): "an integer",
    ("irate_box/hub/server.py", "'admin' if admin else ''"): "two constants",
    ("irate_box/hub/server.py", "'ok' if vouched else ''"): "two constants",
    ("irate_box/hub/store.py", "DEV_CORS"): "the administrator's environment setting, one origin",
    ("irate_box/hub/store.py", "str(size)"): "an integer",
    ("irate_box/hub/appstore.py", "str(length)"): "an integer",
    ("irate_box/hub/server.py", "str(size)"): "an integer",
    ("irate_box/hub/server.py", "str(end - start + 1)"): "an integer",
    (
        "irate_box/hub/server.py",
        "f'{ADMIN_SEEN_COOKIE}={admin_seen_value()}; Path=/; Max-Age={ADMIN_SEEN_HOURS * 3600}; HttpOnly; SameSite=Strict' + ('; Secure' if self._https() else '')",
    ): "a cookie from a constant name, a token the hub made and numbers",
    ("irate_box/hub/store.py", "value"): "through validate.header_value in _send (the call is on the line above)",
}

CONF_MODULES = {
    "irate_box/hub/hotspot.py",
    "irate_box/hub/wificountry.py",
    "irate_box/root/ap.py",
    "irate_box/root/wifijoin.py",
    "irate_box/root/usbgadget.py",
}
CONF_ALLOWED = {
    ("irate_box/root/ap.py", "id={CONNECTION}"): "a module constant",
    (
        "irate_box/root/ap.py",
        "interface-name={ap_iface(plan)}",
    ): "the radio's own interface name, from the root's scan of its devices",
    ("irate_box/root/ap.py", "band={band}"): "one of two fixed words chosen by the plan",
    ("irate_box/root/ap.py", "address1={apmode.ADDRESS}/{NETWORK.prefixlen}"): "module constants",
    (
        "irate_box/root/ap.py",
        "ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file={DNSMASQ_CONF} --",
    ): "module constants",
    ("irate_box/root/ap.py", "OriginalName={SHARED_IFACE}"): "a module constant",
    ("irate_box/root/ap.py", "Name={SHARED_IFACE}"): "a module constant",
    ("irate_box/root/ap.py", "ExecStart={CODE}/irate-box hub_control ap-boot"): "a module constant",
    ("irate_box/root/ap.py", "--on-active={DEADMAN}"): "a module constant",
    (
        "irate_box/root/ap.py",
        "{k.split('.', 1)[1]}={_keyfile_value(v)}",
    ): "the key is the code's own dotted name; the value is written by the keyfile writer",
    ("irate_box/root/wifijoin.py", "id={_escape(name)}"): "wifijoin's own keyfile escape (the name is checked first)",
    ("irate_box/root/wifijoin.py", "uuid={uid}"): "a uuid made here",
    ("irate_box/root/wifijoin.py", "autoconnect-priority={PRIORITY}"): "a module constant",
    (
        "irate_box/root/wifijoin.py",
        "key-mgmt={security}",
    ): "one of three words, chosen from a fixed table after the check",
    (
        "irate_box/root/wifijoin.py",
        "psk={_escape(psk)}",
    ): "wifijoin's own keyfile escape (the password is checked first)",
    ("irate_box/root/usbgadget.py", "interface={IFACE}"): "a module constant",
    ("irate_box/root/usbgadget.py", "dhcp-range={DHCP_FIRST},{DHCP_LAST},{NETMASK},12h"): "module constants",
    ("irate_box/root/usbgadget.py", "dhcp-leasefile={RUN / 'dnsmasq.leases'}"): "a module constant",
    ("irate_box/root/usbgadget.py", "--conf-file={conf}"): "a path made here",
    (
        "irate_box/hub/wificountry.py",
        "{SET_HERE}\\noptions cfg80211 ieee80211_regdom={validate.country(code)}\\n",
    ): "validate.country",
}
SAFE_HEADER_CALLS = {"validate.header_value", "validate.content_disposition", "_drop_disposition"}
URL_ATTRIBUTES = {"href", "src", "action", "formaction", "poster", "data", "srcset", "xlink:href", "content"}
URL_ATTRIBUTES_ALLOWED = {
    (
        "irate_box/hub/server.py",
        "tile['href']",
    ): "manifests.py checks every tile and entry address with validate.local_path",
    (
        "irate_box/hub/server.py",
        "href",
    ): "an entry's address, checked by manifests.py with validate.local_path; or a menu's own",
    ("irate_box/hub/server.py", "target"): "the hub's own address (HUB_URL or HOTSPOT_URL), from its settings",
    ("irate_box/hub/server.py", "nxt"): "checked by validate.local_path just above",
    (
        "irate_box/library/guide.py",
        "href",
    ): "a link in the guide's own source: javascript: and data: forms are dropped just above; the rest are http(s), mailto, a path or an anchor",
    ("irate_box/hub/appstore.py", "link"): "an obtainium:// address built here from quote()d JSON",
    ("irate_box/hub/appstore.py", "_on_this_hub(file['url'])"): "validate.local_path",
    ("irate_box/hub/appstore.py", "_on_this_hub(file['url']) or '#'"): "validate.local_path",
}


def dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def is_constant_name(node):
    return (isinstance(node, ast.Name) and node.id.isupper()) or (
        isinstance(node, ast.Attribute) and node.attr.isupper()
    )


def check_header(path, call, failures):
    if len(call.args) != 2:
        return
    value = call.args[1]
    source = ast.unparse(value)
    if isinstance(value, ast.IfExp):
        for branch in (value.body, value.orelse):
            check_header(
                path, ast.Call(func=call.func, args=[call.args[0], branch], keywords=[], lineno=call.lineno), failures
            )
        return
    if isinstance(value, ast.Constant):
        return
    if isinstance(value, ast.Call) and dotted(value.func) in SAFE_HEADER_CALLS:
        return
    if (
        isinstance(value, ast.Call)
        and dotted(value.func) == "str"
        and value.args
        and isinstance(value.args[0], ast.Call)
        and dotted(value.args[0].func) in ("len", "int")
    ):
        return
    if (path, source) in SEND_HEADER_ALLOWED:
        return
    failures.append(
        f"{path}:{call.lineno}: send_header value {source[:80]!r} is not a constant or a shared writer's result"
    )


def pieces_of(node):
    """The literal text before each formatted value of an f-string or a + chain: (text so far, value node)."""
    if isinstance(node, ast.JoinedStr):
        text = ""
        for part in node.values:
            if isinstance(part, ast.Constant):
                text += str(part.value)
            elif isinstance(part, ast.FormattedValue):
                yield text, part.value
                text = ""
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        chain = []

        def flatten(item):
            if isinstance(item, ast.BinOp) and isinstance(item.op, ast.Add):
                flatten(item.left)
                flatten(item.right)
            else:
                chain.append(item)

        flatten(node)
        text = ""
        for item in chain:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                text += item.value
            else:
                yield text, item
                text = ""


def attribute_context(before):
    """(attribute name, its opening quote or "") when the text so far ends inside an attribute value
    of an open tag, else None (the value lands in element text)."""
    start = before.rfind("<")
    if start < 0 or start < before.rfind(">"):
        return None
    rest = re.sub(r"""([\w:-]+)\s*=\s*(?:"[^"]*"|'[^']*')""", "", before[start:])
    found = re.search(r"""([\w:-]+)\s*=\s*(["']?)[^"'\s>]*$""", rest)
    return (found.group(1).lower(), found.group(2)) if found else None


def check_html(path, node, failures):
    for before, value in pieces_of(node):
        inner = value
        if isinstance(inner, ast.Call) and dotted(inner.func) == "validate.html_text":
            argument = ast.unparse(inner.args[0]) if inner.args else ""
        else:
            continue
        found = attribute_context(before)
        if not found:
            continue
        attribute, quote = found
        if quote != '"':
            failures.append(
                f"{path}:{node.lineno}: html_text in the attribute {attribute!r}, which is not inside double quotes"
            )
        elif attribute in URL_ATTRIBUTES and (path, argument) not in URL_ATTRIBUTES_ALLOWED:
            failures.append(
                f"{path}:{node.lineno}: html_text({argument}) in {attribute!r} holds an address: check it with validate.local_path or validate.url, then list it"
            )


def main():
    failures = []
    for source in sorted((REPO / "irate_box").rglob("*.py")):
        path = str(source.relative_to(REPO))
        tree = ast.parse(source.read_text(), str(source))
        for node in ast.walk(tree):
            if path != "irate_box/validate.py":
                if isinstance(node, ast.Call) and dotted(node.func) == "html.escape":
                    failures.append(f"{path}:{node.lineno}: html.escape: use validate.html_text")
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "html"
                    and any(alias.name == "escape" for alias in node.names)
                ):
                    failures.append(f"{path}:{node.lineno}: from html import escape: use validate.html_text")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "send_header":
                check_header(path, node, failures)
            if isinstance(node, (ast.JoinedStr, ast.BinOp)):
                check_html(path, node, failures)
            if path in CONF_MODULES and isinstance(node, ast.JoinedStr):
                values = node.values
                for index, part in enumerate(values[:-1]):
                    if (
                        isinstance(part, ast.Constant)
                        and isinstance(part.value, str)
                        and part.value.endswith("=")
                        and isinstance(values[index + 1], ast.FormattedValue)
                    ):
                        value = values[index + 1].value
                        safe = (
                            (
                                isinstance(value, ast.Call)
                                and dotted(value.func).split(".")[0] in ("validate", "checks", "int")
                            )
                            or (isinstance(value, ast.Call) and dotted(value.func) in ("_keyfile_value", "_escape"))
                            or is_constant_name(value)
                        )
                        text = "".join(
                            piece.value if isinstance(piece, ast.Constant) else "{" + ast.unparse(piece.value) + "}"
                            for piece in values
                        )
                        key = next((k for k in CONF_ALLOWED if k[0] == path and k[1] in text), None)
                        if not safe and key is None:
                            failures.append(
                                f"{path}:{node.lineno}: a configuration line {text[:70]!r} writes a value that is not through a writer: use validate.config_line"
                            )
                        break
    for line in sorted(set(failures)):
        print("FAIL " + line)
    print(f"failures: {len(set(failures))}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
