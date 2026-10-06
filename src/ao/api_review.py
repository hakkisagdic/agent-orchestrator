"""ao-api-review: one answer from an OpenAI-compatible chat API, run by ao as a tool reviewer (API-REVIEWER).

ao hands a tool reviewer the review prompt, which carries the candidate's diff, and a file to answer
in (docs/adapters.md, "A reviewer run as a tool"). This one asks a provider's `/chat/completions`
once, with the prompt as the only message, and writes the answer it gets. It has no tools: the
model reads what the prompt carries and nothing else, so there is nothing to deny it.

The provider is the machine's. `review.api_providers` names each by a word, with its base URL and
the environment variable that holds its key; the model is `<provider>/<model>`. The key is read
from that variable at the moment it is sent, sent to that URL alone - over HTTPS, or to this
machine with no proxy between - and never carried through a redirect. It is never written anywhere.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import __version__, lib as A

UTF8 = "utf-8"
USAGE, FAILED = 2, 1
LOOPBACK = ("127.0.0.1", "localhost", "::1")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, not a place to send the key."""

    def redirect_request(self, *args, **kwargs):
        return None


def providers(entries):
    """{word: (base URL, key variable)} from `review.api_providers`, or ValueError naming the entry that is not one."""
    table = {}
    for entry in entries or []:
        words = str(entry).split()
        if len(words) != 3:
            raise ValueError(f"{entry!r} in review.api_providers is not a word, a base URL and the environment "
                             "variable that holds its key")
        name, url, variable = words
        parsed = urllib.parse.urlparse(url)
        if not (parsed.scheme == "https" or parsed.scheme == "http" and parsed.hostname in LOOPBACK) \
                or not parsed.hostname:
            raise ValueError(f"{name}'s base URL {url} is not https: its key would leave this machine unprotected")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
            raise ValueError(f"{name}'s key is named {variable!r}, which is no environment variable")
        table[name] = (url.rstrip("/"), variable)
    return table


def _scrub(text, key):
    """`text` with the key taken out, as written and as Python escapes it in a repr (API-REVIEWER-3)."""
    for form in sorted({key, repr(key)[1:-1], key.encode("unicode_escape").decode("ascii")}, key=len, reverse=True):
        if form:
            text = text.replace(form, "<key>")
    return text


def _content(answer):
    """The text of the first choice's message: a string, or the text parts of a list of them."""
    try:
        content = answer["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


def main(argv=None):
    A.utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--model", required=True, help="<provider>/<model>, the provider named in review.api_providers")
    parser.add_argument("--diff-file", required=True, help="the candidate ao wrote; the prompt carries the same diff")
    parser.add_argument("--output", required=True, help="where the answer is written")
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds to wait for the answer")
    parser.add_argument("prompt", help="the review prompt, or - to read it from standard input")
    args = parser.parse_args(argv)

    def refuse(text, code=USAGE):
        print(f"{parser.prog}: {text}", file=sys.stderr)
        return code

    provider, _, model = args.model.partition("/")
    if not provider or not model:
        return refuse(f"--model is {args.model!r}; it names <provider>/<model>")
    from . import settings
    try:
        table = providers(settings.get({}, "review.api_providers"))
    except ValueError as exc:
        return refuse(str(exc))
    if provider not in table:
        return refuse(f"review.api_providers names no {provider}; the machine's providers are "
                      f"{', '.join(sorted(table)) or 'none'}")
    url, variable = table[provider]
    key = os.environ.get(variable)
    if not key:
        return refuse(f"{variable} is not set, and it holds {provider}'s key")
    # A header carries printable ASCII alone, and the error that refuses one names its value: a key holding a line
    # break was printed with it (API-REVIEWER-2). What the key holds is not said.
    if not re.fullmatch(r"[\x20-\x7e]+", key):
        return refuse(f"{variable} holds a character outside printable ASCII, which no header carries")
    if not os.path.isfile(args.diff_file):
        return refuse(f"the candidate {args.diff_file} is not there")
    prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode(UTF8)
    request = urllib.request.Request(url + "/chat/completions", data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}",
        "User-Agent": f"ao/{__version__}"})
    # A provider on this machine is asked directly. A proxy the environment named took a request for 127.0.0.1, its
    # key in plain text, to wherever the proxy was (API-REVIEWER-2); one over HTTPS passes a proxy as a tunnel, which
    # carries the key encrypted.
    handlers = [_NoRedirect()]
    if urllib.parse.urlparse(url).hostname in LOOPBACK:
        handlers.append(urllib.request.ProxyHandler({}))
    try:
        opener = urllib.request.build_opener(*handlers)
        with opener.open(request, timeout=args.timeout) as response:
            answer = json.loads(response.read().decode(UTF8, "replace"))
    except urllib.error.HTTPError as exc:
        try:
            said = _scrub(exc.read(300).decode(UTF8, "replace"), key) if exc.fp else ""
        except Exception:                           # a body cut short is no reason to say less than the status
            said = ""
        return refuse(f"{provider} answered HTTP {exc.code}" + (f": {' '.join(said.split())}" if said else ""), FAILED)
    except Exception as exc:
        # Every failure of the exchange is said with the key taken out: http.client's protocol errors are none of
        # URLError, OSError or ValueError, and a provider that put the key in a malformed status line had it printed
        # in the traceback (API-REVIEWER-3).
        return refuse(f"{provider} could not be asked ({type(exc).__name__}: {_scrub(str(exc), key)})", FAILED)
    text = _content(answer)
    if not text.strip():
        return refuse(f"{provider} gave no answer for {model}", FAILED)
    with open(args.output, "w", encoding=UTF8, newline="\n") as fh:
        fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
