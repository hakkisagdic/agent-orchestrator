"""A request carrying a credential in a header leaves by one opener, which follows no redirect (HUNT-LEADS-SEC).

urllib answers a 301, 302 or 303 by asking the place the answer names, with every header of the request but its
content headers - an `Authorization` among them - and to another host as readily as to the same one: measured on
2026-10-12, a POST carrying `Authorization: Bearer` to a listener on 127.0.0.1 that answered 302 reached a second
listener as a GET with the same header. The A2A bridge carried an agent's header that way (A2A-HARDEN), the account
lookup carried the OIDC access token a harness's CLI holds (HUNT-LEADS-SEC), and the API reviewer held its key
back with a copy of the handler below. One handler now, for all three.

A redirect is an answer here: urllib raises it as the HTTPError of its status, and the caller says that status -
and, where it prints for a person, to write the URL the endpoint moved to where the endpoint is named. A credential
written into a URL's path - Telegram's bot token, formsubmit's alias - is not carried by a redirect: urllib asks the
place the answer names, and the token was in the place it left.
"""
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Follow no redirect: an answer that sends the request elsewhere is an error, said as its HTTP status."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def opener(*handlers):
    """An opener of urllib's own handlers and `handlers`, following no redirect.

    `build_opener` puts NoRedirect in place of its redirect handler, as it puts a ProxyHandler given here in place of
    the one that reads the environment's proxies.
    """
    return urllib.request.build_opener(NoRedirect, *handlers)


OPENER = opener()


def is_redirect(code):
    """Whether an HTTP status is one of the 3xx answers this opener does not follow."""
    return isinstance(code, int) and 300 <= code < 400
