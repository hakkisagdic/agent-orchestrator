"""No tracked file names the private projects ao grew inside, their tracker or their owner (NEUTRAL-NAMES).

ao is public, and it was written inside one person's private projects. Their names reached its
public surfaces: the landing page showed one project's board, the profiles table said where each
profile ran, docs/sources.md quoted real tracker records, and tests signed waivers with the
owner's first name. A sample needs a name, and `acme-api`, `ACME-` and `alice (owner)` serve.

This reads every tracked file, and its path, and fails on a private project's name, the ticket
prefix of their tracker or the owner's first name. The files are the ones git lists in a checkout.
A copy with no git of its own, which is where a landing is tested, is read whole, less what its
.gitignore leaves out and the caches a run writes. Text is folded before it is matched - no case,
no accents, the Turkish dotless i read as i - so every spelling of a name has one form. A project's
name is found inside a longer word. The prefix and the first name count only as words of their
own: the repository's address is an account whose name starts with the first name, and it is not
a use of it. The names are put together at runtime, so this file does not hold them whole.

A name stays only where ALLOWED lets it, and each allowance says why.
"""
import fnmatch
import re
import subprocess
import unicodedata
from pathlib import Path

from tests import conftest

ROOT = Path(__file__).resolve().parent.parent

PROJECTS = ("vol" + "trai", "du" + "kkan")     # the private products, folded
PREFIX = "d" + "kk"                            # the ticket prefix of their tracker, folded
OWNER = "hak" + "ki"                           # the owner's first name, folded from either spelling
NAMES = PROJECTS + (PREFIX, OWNER)
WHAT = dict([(name, "a private project's name") for name in PROJECTS]
            + [(PREFIX, "the private tracker's ticket prefix"), (OWNER, "the owner's first name")])

# Where a name may stay - a file, or a directory written with its trailing "/" - which names, and why.
ALLOWED = {
    "docs/audit/": (NAMES, "an audit report quotes the tree and the machine it audited; the audit "
                           "leaves the public documents in a slice of its own"),
    "docs/backlog.md": (NAMES, "closed rows and dated notes record the project each measurement was "
                               "taken in; the backlog leaves the public documents in a slice of its own"),
    "LICENSE": ((OWNER,), "the copyright notice names the copyright holder, and the MIT licence "
                          "requires the notice"),
    "pyproject.toml": ((OWNER,), "the package metadata names its author"),
}

# What a run writes and git never tracks, whether or not a .gitignore says so.
CACHES = (".git", ".pytest_cache")


def _fold(text):
    """`text` without case or accents, the Turkish dotless i read as i: one form for every spelling."""
    text = unicodedata.normalize("NFKD", text.replace("\u0131", "i"))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()


def _pattern(name):
    """A project's name inside any word; the prefix and the first name only as words of their own."""
    if name in PROJECTS:
        return re.compile(re.escape(name))
    return re.compile(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])")


def _ignore_rules(root):
    """The patterns of `root`'s .gitignore that leave something out: no blanks, comments or negations."""
    try:
        lines = (root / ".gitignore").read_text(encoding="utf-8").split("\n")
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith(("#", "!"))]


def _ignored(relative, rules):
    """Whether `relative` is a cache, or a rule leaves it out as git reads the simple rules.

    A rule with a slash before its end is anchored at the root and covers what lies under the path
    it names. Any other covers a name at every depth, and with a trailing slash only a directory's.
    """
    parts = relative.split("/")
    if any(part in CACHES for part in parts):
        return True
    for rule in rules:
        body = rule.strip("/")
        if rule.startswith("/") or "/" in body:
            if any(fnmatch.fnmatchcase("/".join(parts[:n]), body) for n in range(1, len(parts) + 1)):
                return True
        elif any(fnmatch.fnmatchcase(part, body) for part in (parts[:-1] if rule.endswith("/") else parts)):
            return True
    return False


def _tracked(root):
    """The files under `root` that git tracks, as paths relative to it.

    In a checkout that is git's own list, so a note nobody added is not read. Elsewhere - no git,
    or `root` inside another repository - it is every file the .gitignore and the caches leave.
    """
    root = Path(root)
    try:
        top = subprocess.run([conftest.GIT, "rev-parse", "--show-toplevel"], cwd=str(root),
                             capture_output=True, text=True, check=True).stdout.strip()
        if top and Path(top).resolve() == root.resolve():
            listed = subprocess.run([conftest.GIT, "ls-files", "-z"], cwd=str(root), capture_output=True,
                                    check=True).stdout.decode("utf-8", "surrogateescape")
            return sorted(name for name in listed.split("\0") if name and (root / name).is_file())
    except (OSError, subprocess.CalledProcessError):
        pass
    rules = _ignore_rules(root)
    walked = (path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
    return sorted(relative for relative in walked if not _ignored(relative, rules))


def _allowed(relative):
    """The names `relative` may hold: those of each allowance that covers it."""
    return {name for place, (names, _) in ALLOWED.items()
            if relative == place or (place.endswith("/") and relative.startswith(place)) for name in names}


def _findings(root):
    """`path:line: what` for each name a tracked file under `root` holds where ALLOWED does not let it.

    Line 0 is the file's path.
    """
    root = Path(root)
    patterns = [(name, _pattern(name)) for name in NAMES]
    out = []
    for relative in _tracked(root):
        allowed = _allowed(relative)
        text = (root / relative).read_bytes().decode("utf-8", "replace")
        for number, line in enumerate([relative] + text.split("\n")):
            folded = _fold(line)
            out.extend(f"{relative}:{number}: {WHAT[name]}" for name, pattern in patterns
                       if name not in allowed and pattern.search(folded))
    return out


def test_no_tracked_file_names_a_private_project_its_tracker_or_its_owner():
    found = _findings(ROOT)

    assert found == [], "\n".join(found)


def test_each_allowance_covers_a_tracked_path_holds_only_these_names_and_says_why():
    tracked = _tracked(ROOT)

    for place, (names, why) in ALLOWED.items():
        assert why.strip() and set(names) <= set(NAMES), place
        assert any(path == place or (place.endswith("/") and path.startswith(place)) for path in tracked), \
            f"{place} is allowed and nothing tracked is there any more: drop the allowance"


def test_every_spelling_is_one_name_a_path_counts_and_what_is_allowed_or_ignored_is_not_read(tmp_path):
    project, product = PROJECTS
    decomposed = product[0].upper() + "u\u0308" + product[2:]                # a u, then a combining diaeresis
    composed = product[0] + "\u00fc" + product[2:4] + "\u00e2" + product[5:]   # each mark inside its letter
    dotless = OWNER[:-1].capitalize() + "\u0131"                              # the Turkish dotless i
    dotted = OWNER[:-1].upper() + "\u0130"                                    # a capital I with a dot
    sample = {
        ".gitignore": "# state and logs\n__pycache__/\n.ao/\n*.log\n",
        ".ao/config.json": f'{{"project": "{project}"}}\n',
        ".pytest_cache/v/cache/nodeids": f"{project}\n",
        "run.log": f"{project}\n",
        "src/__pycache__/x.pyc": f"{project}\n",
        "README.md": f"Built for {project.upper()}.\n",
        f"notes/{project}-plan.md": "the path is read as well\n",
        "docs/shop.md": f"# {decomposed} notes\n{composed}\n",
        "docs/sources.md": f"{PREFIX.upper()}-12  fix the login\n",
        "tests/test_waive.py": f'by = "{dotless} (owner)"\nby = "{dotted}"\n',
        "docs/handle.md": f"github.com/{OWNER}smith/tool, and a Turkish word that starts alike: {OWNER}nda\n",
        "LICENSE": f"Copyright (c) 2026 {OWNER.capitalize()} Smith\n{project}\n",
        "docs/audit/report.md": f"{project} {PREFIX.upper()}-1 {OWNER}\n",
    }
    for name, text in sample.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")

    assert _findings(tmp_path) == [
        "LICENSE:2: a private project's name",
        "README.md:1: a private project's name",
        "docs/shop.md:1: a private project's name",
        "docs/shop.md:2: a private project's name",
        "docs/sources.md:1: the private tracker's ticket prefix",
        f"notes/{project}-plan.md:0: a private project's name",
        "tests/test_waive.py:1: the owner's first name",
        "tests/test_waive.py:2: the owner's first name",
    ]


def test_in_a_checkout_only_what_git_tracks_is_read(tmp_path):
    for name in ("added.md", "not-added.md"):
        (tmp_path / name).write_text(f"{PROJECTS[0]}\n", encoding="utf-8")
    subprocess.run([conftest.GIT, "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run([conftest.GIT, "add", "added.md"], cwd=str(tmp_path), check=True)

    assert _findings(tmp_path) == ["added.md:1: a private project's name"]
