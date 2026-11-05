# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Fetches from the internet that connect only to a public address, checked at connect.

`validate.public_url` looks a name up once; a fetch that then lets urllib look it up again can be
sent somewhere else (a name that answers a public address first and a private one next). Here the
connection itself does the looking-up (`validate.connect_public`): every answer must be public, and
the socket goes to one of those answers. Every redirect is checked again, the same way. Stdlib only.

    opener = webfetch.opener()                     # https only
    opener = webfetch.opener(schemes=("http", "https"))
    with opener.open(request, timeout=30) as response: ...

A fetch of an address the owner typed in, which may be on their own network, does not use this.
"""

import http.client
import urllib.error
import urllib.request

from irate_box import validate


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, resolve=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._resolve = resolve

    def _connect_socket(self):
        try:
            return validate.connect_public(self.host, self.port, self.timeout, resolve=self._resolve)
        except validate.Invalid as error:
            raise OSError(str(error)) from None

    def connect(self):
        if self._tunnel_host:  # through a proxy: the proxy's own address is the one connected to
            return super().connect()
        self.sock = self._connect_socket()


class _PinnedHTTPSConnection(_PinnedHTTPConnection, http.client.HTTPSConnection):
    def connect(self):
        if self._tunnel_host:
            return http.client.HTTPSConnection.connect(self)
        sock = self._connect_socket()
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, resolve=None):
        super().__init__()
        self._resolve = resolve

    def http_open(self, request):
        return self.do_open(_PinnedHTTPConnection, request, resolve=self._resolve)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, resolve=None):
        super().__init__()
        self._resolve = resolve

    def https_open(self, request):
        return self.do_open(_PinnedHTTPSConnection, request, context=self._context, resolve=self._resolve)


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, resolve=None, schemes=("https",)):
        super().__init__()
        self._resolve = resolve
        self._schemes = schemes

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate.public_url(newurl, "The redirect address", resolve=self._resolve, schemes=self._schemes)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener(resolve=None, schemes=("https",), redirect=None):
    """An opener whose every connection, redirects included, goes to a public address. `resolve`
    is a function like socket.getaddrinfo, called for each connection (so a caller can hand over
    one that looks its own setting up at call time); by default, socket.getaddrinfo. `redirect`
    is a redirect handler of the caller's own, used in place of the one made here."""
    return urllib.request.build_opener(
        _PinnedHTTPHandler(resolve), _PinnedHTTPSHandler(resolve), redirect or _CheckedRedirect(resolve, schemes)
    )
