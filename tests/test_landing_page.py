"""What the landing page states about ao is checked against the tree that ships it (LANDING-REFRESH).

`.github/workflows/pages.yml` publishes `index.html` on every push to main, and until this module the
suite never read it. The page carried its facts as prose, so a release could ship a page naming a
command the parser has never had or an adapter level its adapter file no longer declares - the drift
tests/test_readme_adapters.py exists for, on the one surface a stranger reads first. The page at 4cb79d7
was itself such a page: it still sold ao as "attach to the agent you are already running", named codex
and qoder untested after their adapters moved to partial, listed 15 of the 28 adapters ao ships, and
showed no command at all.

The workflow catches one fact about the page - that it loads nothing from another host - and catches it
after the push. These read the same page for the claims the tree can check: the version it names, the
level each adapter declares, the commands it invites a reader to type, in prose and in its code blocks.
A fifth is a property of the page rather than a claim about the code: every string of its copy ships in
both languages, since a page half-translated reads as a page abandoned. A sixth reads the sentence the
page opens with against the three other places the package states what it is - the build metadata, the
imported module, the formula a tap publishes from - because the page was not the only surface still
selling the positioning ao grew out of.
"""
import contextlib
import io
import re
import shlex
from pathlib import Path

from ao import __version__, cli, lib as A

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"
PILL = re.compile(r'<span class="pill">(full|partial|documented|untested)</span></td><td>(.*?)<br>', re.S)
TAG = re.compile(r"<[^>]+>")
BLOCK = re.compile(r'<pre class="(?:term|sh)"[^>]*>(.*?)</pre>', re.S)
TERM_PAIR = re.compile(
    r'<pre class="term" lang-en>(.*?)</pre>\s*<pre class="term" lang-tr>(.*?)</pre>', re.S
)
UNTAGGED = re.compile(r'<(?:pre class="term"|span class="c"|th\b)(?![^>]*\blang-(?:en|tr)\b)[^>]*>')
CODE = re.compile(r'<(?:code|span class="k")>ao ([a-z][a-z-]*)')
LANGUAGE = re.compile(r'<(\w+)[^>]*?\blang-(en|tr)\b')
EXTERNAL = re.compile(r'<(?:script|link|img)[^>]+(?:src|href)="https?://')
EMPTY = re.compile(r'<(\w+)[^>]*?\blang-(?:en|tr)\b[^>]*>\s*</\1>')
TITLE = re.compile(r"<title>agent-orchestrator — (.+?)</title>")
H1 = re.compile(r"<h1>\s*<span lang-en>(.*?)</span\s*>\s*<span lang-tr>(.*?)</span>\s*</h1>", re.S)
RETIRED = re.compile(r"attach to the (?:coding )?agent you (?:are )?already running", re.I)
OVERSTATED_HOOK = re.compile(
    r"only a granted (?:change|candidate) (?:lands|commits)"
    r"|lets only that (?:change )?commit"
    r"|yalnız yetkilendirilen iniyor"
    r"|yalnız o değişikliğin inmesine izin verir",
    re.I,
)
FORMULA_VERSION = re.compile(r"/refs/tags/v(\d+\.\d+\.\d+)\.tar\.gz")
IDENTITY = {                      # the other places the package says what it is
    "pyproject.toml": ROOT / "pyproject.toml",
    "src/ao/__init__.py": ROOT / "src" / "ao" / "__init__.py",
    "packaging/homebrew/agent-orchestrator.rb": ROOT / "packaging" / "homebrew" / "agent-orchestrator.rb",
}


def _page():
    return PAGE.read_text(encoding="utf-8")


def _typed_commands(text):
    """Each `ao …` line of the page's code blocks, as the words a reader would type. A block's own
    output lines are not commands, and neither is a comment."""
    for block in BLOCK.findall(text):
        for line in TAG.sub("", block).splitlines():
            line = line.strip().lstrip("$").strip()
            if not line or line.startswith("#"):
                continue
            try:
                words = shlex.split(line, comments=True)
            except ValueError:
                continue
            if words[:1] == ["ao"]:
                yield words


def test_the_landing_page_loads_nothing_from_another_host():
    """LANDING-REFRESH-1: the workflow refuses an external script, stylesheet or image, because a page that
    breaks when a CDN moves is a page nobody notices is broken. That check runs on the pushed tree; this
    one runs before it, on the page a contributor has just edited."""
    assert EXTERNAL.findall(_page()) == []


def test_the_landing_page_names_the_version_the_package_is_built_at():
    """LANDING-REFRESH-2: the page states the version twice, in its header and its footer, and a release that
    forgets it tells the reader about a build that no longer exists."""
    assert sorted(set(re.findall(r"\bv(\d+\.\d+\.\d+)\b", _page()))) == [__version__]


def test_the_landing_page_groups_each_adapter_as_its_adapter_declares_it():
    """LANDING-REFRESH-3: the table is the README's table in another shape - each level naming the adapters
    whose adapter declares that level, and no level missing. A cloud agent's adapter is left out as
    `ao adapters` leaves it out: it names no harness this page can review with."""
    declared = {}
    for name, adapter in A.package_adapters().items():
        if adapter.get("kind") != "cloud":
            declared.setdefault(adapter.get("verified"), set()).add(name)
    shown = {level: {name.strip() for name in TAG.sub("", cell).split("·") if name.strip()}
             for level, cell in PILL.findall(_page())}

    assert shown == declared, {level: (sorted(shown.get(level, set())), sorted(declared.get(level, set())))
                               for level in set(shown) | set(declared) if shown.get(level) != declared.get(level)}


def test_every_command_name_on_the_landing_page_is_one_ao_has():
    """LANDING-REFRESH-4: the page names commands in prose - `ao catchup` here, `ao gain` there - and in the
    panel that walks a change through them, and a name ao does not have is a reader hitting an error on
    the first try."""
    commands = next(action.choices for action in cli.build_parser()._actions if isinstance(action.choices, dict))

    assert sorted(set(CODE.findall(_page())) - set(commands)) == []


def test_every_command_the_landing_page_shows_a_reader_type_parses():
    """LANDING-REFRESH-5: the setup block and the sample run are the page's promise - five minutes to the
    first grant - so each line goes through ao's own parser, which also holds `--profile` to the
    profiles that exist and `hooks` to the subcommands it has."""
    problems = []
    for words in _typed_commands(_page()):
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                cli.build_parser().parse_args(words[1:])
        except SystemExit as exit:
            problems.append("{} (exit {})".format(" ".join(words), exit.code))

    assert problems == []


def test_every_string_on_the_landing_page_ships_in_both_languages():
    """LANDING-REFRESH-6: the page's copy is paired - each English element written next to its Turkish one -
    and a page half-translated reads as a page abandoned. So the elements are checked to alternate
    rather than merely to number the same on both sides: dropping one Turkish element leaves the counts
    even and the pairing broken, an element with no words in it counts as missing them, and a terminal
    sample, shell comment or table heading without a language attribute stays visible in English when the
    reader switches to Turkish."""
    text = _page()
    kinds = [kind for _, kind in LANGUAGE.findall(text)]
    terms = TERM_PAIR.findall(text)

    assert kinds, "the page carries no bilingual copy"
    assert kinds == ["en", "tr"] * (len(kinds) // 2)
    assert EMPTY.findall(text) == []
    assert UNTAGGED.findall(text) == []
    assert len(terms) == 2
    for en_term, tr_term in terms:
        assert len(en_term.splitlines()) == len(tr_term.splitlines())
        assert en_term != tr_term


def test_the_package_sells_what_the_landing_page_sells():
    """LANDING-REFRESH-7: the sentence the page opens with is the package's own description, in the build
    metadata, in the module every command imports, and in the formula a tap publishes from. Those four
    drifted together: at 4cb79d7 all of them still sold ao as attaching to a running agent, so fixing the
    page alone would have left the same stale claim one surface further down. Each states the page's
    headline sentence, and none states the retired one."""
    tagline = TITLE.search(_page()).group(1)
    assert tagline, "the page carries no headline sentence to hold the other surfaces to"

    problems = []
    for what, path in sorted(IDENTITY.items()):
        text = path.read_text(encoding="utf-8")
        stale = RETIRED.search(text)
        if stale:
            problems.append("{} still says {!r}".format(what, stale.group(0)))
        if tagline.lower() not in text.lower():
            problems.append("{} does not say {!r}".format(what, tagline))

    assert problems == [], problems


def test_the_landing_page_does_not_claim_the_hook_alone_stops_an_ungranted_commit():
    """LANDING-REFRESH-8: an in-scope implementer whose harness allows git commands can run
    `git commit --no-verify`; the pre-commit hook refuses a plain `git commit`, and `ao doctor` and the
    watchdog flag a commit that went around it. Neither the page nor the package metadata may state that
    only a granted change lands."""
    page = _page()
    en_h1, tr_h1 = H1.search(page).groups()
    surfaces = dict(IDENTITY, **{"index.html": PAGE})
    overstated = {
        what: match.group(0)
        for what, path in sorted(surfaces.items())
        for match in [OVERSTATED_HOOK.search(path.read_text(encoding="utf-8"))]
        if match
    }

    assert overstated == {}
    assert "granted" in en_h1.lower() and "flagged" in en_h1.lower()
    assert "yetkilendirilir" in tr_h1.lower() and "işaretlenir" in tr_h1.lower()


def test_every_install_route_on_the_landing_page_ships_the_version_the_page_names():
    """LANDING-REFRESH-9: the Homebrew formula is updated in a separate commit after the release tag is
    published (`docs/release.md` step 7), so at release time its archive URL still points at the previous
    tag. The page must not tell a reader to run `brew install` while the formula is pinned to an older
    version than the one the page announces."""
    formula = IDENTITY["packaging/homebrew/agent-orchestrator.rb"].read_text(encoding="utf-8")
    pinned = FORMULA_VERSION.findall(formula)

    assert pinned, "the Homebrew formula names no tagged archive"
    if "brew install" in _page():
        assert pinned == [__version__]
