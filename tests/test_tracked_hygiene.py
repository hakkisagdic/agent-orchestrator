"""No tracked file holds a credential, or a home directory of the machine it was written on (TRUST-HYGIENE).

ao is public, and it is written beside agents that print tokens, read key files and quote terminals
whole. A token pasted into a document, a key file added with everything else, a fixture holding a
real credential or a path copied from a terminal is public the moment it is pushed, and stays in the
history after it is deleted.

This reads every tracked file - the list tests/test_public_names.py reads: git's own in a checkout,
the tree less its .gitignore in a copy without git - for three things:

- the credential shapes ao keeps out of what it writes, every one of them (lib.EVIDENCE_RULES,
  docs/safety.md §7b): private keys, Anthropic, OpenAI, GitHub, Slack and AWS keys, JWTs, bearer
  tokens, and a long value assigned to a password, a secret, an API key or an access token, a
  passphrase of words among them (`assigned-secret`);
- a value of 16 characters or more assigned to any name that says it is secret - a key, a secret, a
  token, a password, a credential: `ENCRYPTION_KEY`, `signingKey`, `DEPLOY_SECRET` - with the spread a
  generated secret has: 3.5 bits a character or more, a letter and a digit. A word, a placeholder or a
  run of one letter is not one;
- a home directory of a machine: /Users/<name>, /home/<name>, or a Windows one, C:\\Users\\<name>,
  whichever slashes and case it is written with. A document names a home as `~`, and `<user>` or
  `$USER` stand for anyone's.

An assigned value that is called is code: `password = os.environ.get(...)` names where a secret is
read, and holds none, so neither assignment rule takes one.

A deliberate fixture stays only where tests/hygiene-allowlist.txt names it: the file or directory,
the rule, and what was found - a credential by the start of its SHA-256, so the allowlist holds no
secret, a home by the account's name, in the one file it stands in - with why. An allowance that
covers nothing fails until it is dropped. Every sample below is put together at run time, so this
file holds none of what it seeks.
"""
import base64
import collections
import functools
import hashlib
import math
import re
from pathlib import Path

from ao import lib as A
from tests.test_public_names import ROOT, _tracked

ALLOWLIST = ROOT / "tests" / "hygiene-allowlist.txt"

SECRET_NAME = r"(?:key|secret|token|passw(?:or)?d|pwd|credential)"
# Read from the word that says secret, so `DEPLOY_SECRET`, `aws_access_key_id` and `signingKey` are found
# without trying every word start; the lookahead turns most positions away on their first letter, a third
# of the time the scan took.
ASSIGNMENT = re.compile(r"(?i)(?=[cpkst])" + SECRET_NAME + r"[\w.-]*[\"']?[ \t]*(?::=|=>|[:=])[ \t]*"
                        r"(?P<quote>[\"'`]?)(?P<value>[A-Za-z0-9+/=_.~-]{16,})(?P=quote)")
# The account ends in a letter or a digit, so the full stop of a sentence that ends on a path is not in it.
# `Users` is spelt in either case letter by letter: Windows keeps no case, and /users/ in a URL is no home.
HOME_PATH = re.compile(r"(?:/Users/|/home/|\b[A-Za-z]:[\\/]+[Uu][Ss][Ee][Rr][Ss][\\/]+)(?P<account>[\w.-]*[^\W_])")
# In the order a string is claimed: when two rules find one string, the first names it.
RULES = tuple((rule, re.compile(pattern)) for rule, pattern in A.EVIDENCE_RULES) + (
    ("high-entropy-assignment", ASSIGNMENT), ("home-path", HOME_PATH))
ASSIGNMENTS = ("assigned-secret", "high-entropy-assignment")

Finding = collections.namedtuple("Finding", "path line rule identity")
Allowance = collections.namedtuple("Allowance", "line place rule identity why")


def _bits_per_character(text):
    counts = collections.Counter(text)
    return -sum(count / len(text) * math.log2(count / len(text)) for count in counts.values())


def _generated(value):
    """Whether a value has the spread of a generated secret: 3.5 bits a character or more, a letter and a digit."""
    return _bits_per_character(value) >= 3.5 and any(c.isalpha() for c in value) and any(c.isdigit() for c in value)


def _identity(rule, match):
    """A home by its account's name; anything else by the start of its SHA-256, which says nothing of it."""
    if rule == "home-path":
        return match.group("account")
    found = match.group("value") if rule == "high-entropy-assignment" else match.group(0)
    return "sha256:" + hashlib.sha256(found.encode("utf-8")).hexdigest()[:16]


def _findings_in(relative, text):
    """Each string of `text` a rule finds, once: overlapping strings are one finding, the first rule's."""
    found, claimed = [], []
    for rule, pattern in RULES:
        for match in pattern.finditer(text):
            if rule in ASSIGNMENTS and text.startswith("(", match.end()):
                continue                    # a value that is called: code, naming where a secret is read
            if rule == "high-entropy-assignment" and not _generated(match.group("value")):
                continue
            start, end = match.span("value") if rule == "high-entropy-assignment" else match.span()
            if any(start < taken_end and taken_start < end for taken_start, taken_end in claimed):
                continue
            claimed.append((start, end))
            found.append(Finding(relative, text.count("\n", 0, start) + 1, rule, _identity(rule, match)))
    return found


@functools.lru_cache(maxsize=None)
def _findings(root):
    """Every finding in every tracked file under `root`, a file read as UTF-8 whatever it holds."""
    root = Path(root)
    return tuple(finding for relative in _tracked(root)
                 for finding in _findings_in(relative, (root / relative).read_bytes().decode("utf-8", "replace")))


def _allowances(path):
    """The allowances a file holds, one a line: `<file, or directory/> <rule> <identity> <why>`; # comments."""
    out = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").split("\n"), 1):
        if line.strip() and not line.lstrip().startswith("#"):
            fields = line.split(None, 3)
            out.append(Allowance(number, *(fields + [""] * (4 - len(fields)))))
    return out


def _covers(allowance, finding):
    """A credential's digest is its alone, so it may stand across a directory; an account's name, in one file."""
    place = allowance.place
    across = place.endswith("/") and allowance.rule != "home-path" and finding.path.startswith(place)
    return (finding.path == place or across) \
        and (allowance.rule, allowance.identity) == (finding.rule, finding.identity)


def _unallowed(root, allowlist):
    """`path:line: rule identity` for each finding under `root` no allowance of `allowlist` covers."""
    allowances = _allowances(allowlist)
    return [f"{f.path}:{f.line}: {f.rule} {f.identity} - a deliberate fixture is named in {Path(allowlist).name} "
            f"as `{f.path} {f.rule} {f.identity} <why>`"
            for f in _findings(str(root)) if not any(_covers(allowance, f) for allowance in allowances)]


def _allowlist_problems(root, allowlist):
    """What is wrong with each allowance: a rule no one has, no reason, a home across a directory, nothing to cover."""
    rules, findings, name = {rule for rule, _ in RULES}, _findings(str(root)), Path(allowlist).name
    out = []
    for allowance in _allowances(allowlist):
        if allowance.rule not in rules:
            out.append(f"{name}:{allowance.line}: there is no rule {allowance.rule!r}")
        elif not allowance.why.strip():
            out.append(f"{name}:{allowance.line}: an allowance says why it stands")
        elif allowance.rule == "home-path" and allowance.place.endswith("/"):
            out.append(f"{name}:{allowance.line}: a home-path allowance names a file: an account's name, "
                       "`me` or `dev`, is too common to stand across a directory")
        elif not any(_covers(allowance, finding) for finding in findings):
            out.append(f"{name}:{allowance.line}: {allowance.place} holds no {allowance.rule} {allowance.identity} "
                       "any more: drop the allowance")
    return out


def test_no_tracked_file_holds_a_credential_or_a_home_directory_the_allowlist_does_not_name():
    found = _unallowed(ROOT, ALLOWLIST)

    assert found == [], "\n".join(found)


def test_each_allowance_names_a_rule_says_why_and_still_covers_what_it_names():
    problems = _allowlist_problems(ROOT, ALLOWLIST)

    assert problems == [], "\n".join(problems)


def _samples():
    """{name: text} of files that hold each shape, and of files that only resemble one; built here, at run time."""
    generated = base64.b64encode(hashlib.sha256(b"trust hygiene sample").digest()).decode("ascii").rstrip("=")
    keyed = base64.b64encode(hashlib.sha256(b"an encryption key").digest()).decode("ascii")[:24]
    body = base64.b64encode(hashlib.sha512(b"not a key").digest()).decode("ascii")
    key = "RSA PRIVATE" + " KEY-----"
    return (generated, keyed), {
        "keys/deploy.pem": f"-----BEGIN {key}\n{body}\n-----END {key}\n",
        "ci/env.sh": "export AWS_ACCESS_KEY_ID=" + "AK" + "IA" + "7QK2LXG4RZ5WN3YD\n"
                     + "export GH_TOKEN=" + "gh" + "p_" + "Zx9" * 12 + "\n",
        "config/service.json": '{"name": "svc", "api' + '_key": "' + generated + '"}\n',
        "config/db.yml": "pass" + "word: correct-horse-battery-staple\n",               # words: ao's own rule
        "app/settings.py": "DEPLOY_SEC" + f'RET = "{generated[::-1]}"\n' + "ENCRYPTION" + f'_KEY = "{keyed}"\n',
        "docs/notes.md": "Ran it in /Us" + "ers/alice/work and C:" + "\\Us" + "ers\\Bob\\repo; a log in /ho" + "me/carol.\n",
        "harmless.md": "\n".join([
            "api" + "_key = settings.read_the_key_from_the_environment('X')",      # code: a value that is called
            "pass" + 'word = os.environ.get("DB_PASSWORD")',                        # code: read from the environment
            "primary" + '_key = "account_identifier_column"',                       # words: no digit
            "tok" + 'en = "' + "x" * 40 + '"',                                       # one letter
            "max_tok" + "ens = 12345678901234567890",                                # digits: no letter
            "tok" + 'en: "<your token>"', "sec" + "ret: [redacted:github-token]",    # placeholders
            "sha256 = " + '"' + hashlib.sha256(b"a digest").hexdigest() + '"',       # a digest, not a secret
            "Homes are ~/.ao, /Us" + "ers/<name>/, /ho" + "me/$USER and C:" + "\\Us" + "ers\\...",
            "GitHub lists them at https://api.github.com/users/octocat/repos",      # a URL, not a home
        ]) + "\n",
    }


def test_each_shape_is_found_once_and_what_only_resembles_one_is_not(tmp_path):
    values, samples = _samples()
    for name, text in samples.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")

    found = sorted((f.path, f.line, f.rule) for f in _findings(str(tmp_path)))

    assert all(_generated(value) and _generated(value[::-1]) for value in values)
    assert found == [
        # DEPLOY_SECRET is a secret's name as an environment writes it, which the evidence rule reads first
        # (EVIDENCE-SCAN-2); ENCRYPTION_KEY names none of its words, and only the tracked-file rule finds it.
        ("app/settings.py", 1, "assigned-secret"), ("app/settings.py", 2, "high-entropy-assignment"),
        ("ci/env.sh", 1, "aws-access-key"), ("ci/env.sh", 2, "github-token"),
        ("config/db.yml", 1, "assigned-secret"),
        ("config/service.json", 1, "assigned-secret"),
        ("docs/notes.md", 1, "home-path"), ("docs/notes.md", 1, "home-path"), ("docs/notes.md", 1, "home-path"),
        ("keys/deploy.pem", 1, "private-key"),
    ]
    assert sorted(f.identity for f in _findings(str(tmp_path)) if f.rule == "home-path") == ["Bob", "alice", "carol"]
    assert all(value not in f.identity and value[::-1] not in f.identity
               for f in _findings(str(tmp_path)) for value in values)


def test_an_allowance_covers_only_the_string_it_names_where_it_names_it_and_one_that_covers_nothing_fails(tmp_path):
    _, samples = _samples()
    tree = tmp_path / "tree"
    for name in ("ci/env.sh", "docs/notes.md"):
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(samples[name], encoding="utf-8")
    identity = {f.rule: f.identity for f in _findings(str(tree)) if f.rule != "home-path"}
    allowlist = tmp_path / "allow.txt"
    allowlist.write_text("\n".join([
        "# fixtures",
        f"ci/env.sh aws-access-key {identity['aws-access-key']} a documented example key",
        "docs/notes.md home-path alice a made-up account",
        "docs/ home-path Bob a made-up account, across a directory",
        f"docs/ github-token {identity['github-token']} the token, named where it is not",
        "ci/env.sh home-path carol a made-up account, named where it is not",
        "ci/env.sh slack-token sha256:0000000000000000",
        "ci/ no-such-rule x why",
    ]) + "\n", encoding="utf-8")

    unallowed = [line.split(" - ")[0] for line in _unallowed(tree, allowlist)]

    assert unallowed == [f"ci/env.sh:2: github-token {identity['github-token']}",
                         "docs/notes.md:1: home-path Bob", "docs/notes.md:1: home-path carol"]
    assert _allowlist_problems(tree, allowlist) == [
        "allow.txt:4: a home-path allowance names a file: an account's name, `me` or `dev`, is too common to "
        "stand across a directory",
        "allow.txt:5: docs/ holds no github-token " + identity["github-token"] + " any more: drop the allowance",
        "allow.txt:6: ci/env.sh holds no home-path carol any more: drop the allowance",
        "allow.txt:7: an allowance says why it stands",
        "allow.txt:8: there is no rule 'no-such-rule'",
    ]
