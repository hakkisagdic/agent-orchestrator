# Release checklist

What the person who publishes a release runs, in order, one command at a time.
[packaging/RELEASING.md](../packaging/RELEASING.md) explains why each channel works the way it
does; this page is the list to tick.

Each step says who takes it. A step marked *person* is one only a person takes: publishing and
pushing - ao never grants a push, and PyPI never lets a published version number be used again -
and starting the hosted test runs, which run on the maintainer's word. An agent may prepare every
other step, as a candidate that lands through verify, review and commit-ok like any slice.

*In ao since slice RELEASE-NOTES: this checklist, and [CHANGELOG.md](../CHANGELOG.md), whose
Unreleased section is the draft of the next release's notes (backlog #12).*

The commands below read the version from one variable:

```bash
V=0.5.0
```

## 1. The notes say what ships (anyone)

Every commit since the last tag has its line under Unreleased in CHANGELOG.md, and the README's
command rows and adapter table, in both languages, match `ao --help` and `ao adapters`. No review is
still owed for what ships: `ao catchup --plan` lists each open review waiver and writes nothing, and
releasing over one is the owner's decision, not the checklist's.

```bash
git log --oneline "$(git describe --tags --abbrev=0)"..HEAD
ao catchup --plan
```

## 2. Bump the version (anyone prepares; it lands through ao)

The version is written once, as `__version__` in `src/ao/__init__.py`: `pyproject.toml` and
`ao --version` read it, and the release workflow refuses a tag that differs from it. In
CHANGELOG.md, rename `## [Unreleased]` to `## [<version>] - <date>`, open an empty
`## [Unreleased]` above it, point the `[Unreleased]` link at `v<version>...HEAD` and add a
`[<version>]` link from the previous tag. The clone must still run with nothing installed.

```bash
sed -i.bak "s/^__version__ = .*/__version__ = \"$V\"/" src/ao/__init__.py && rm src/ao/__init__.py.bak
PYTHONPATH=src python3 -m ao --version
./bin/ao adapters
ao verify
ao review
ao commit-ok
ao commit -m "release: v$V"
```

## 3. Push main (person)

The repository's pre-push hook runs the whole suite on this machine before anything leaves it.

```bash
git push origin main
```

## 4. Tests green on Linux, macOS and Windows (person starts the runs; anyone reads them)

The push runs Ubuntu on Python 3.9 and 3.12 by itself. Run all three operating systems on the
support floor and on a current interpreter, on the release commit that main now points at, and
read every run green.

```bash
gh workflow run tests --ref main -f os=all -f python=3.9
gh workflow run tests --ref main -f os=all -f python=3.12
gh run list --workflow tests --limit 8
```

## 5. Tag (person: this publishes)

Pushing a `v*` tag starts `.github/workflows/release.yml`, and PyPI and the GitHub release follow
from it. Tag the commit the runs in step 4 tested, annotated as every earlier tag is:
`git push --follow-tags` sends annotated tags only, so push the tag by its name.

```bash
git tag -a "v$V" -m "ao $V - <the release in one line>" origin/main
git push origin "v$V"
```

## 6. PyPI (automatic on the tag; a person approves and checks)

The release workflow runs the suite on 3.9 and 3.12, builds, refuses a tag that is not the
packaged version and a wheel without its adapters, uploads through Trusted Publishing, and only then
creates the GitHub release, whose log prints the Homebrew sha256. If the `pypi` environment asks
for an approval, a person gives it in the run. The first release of a new PyPI project needs the
one-time pending publisher, set in a browser by the account owner
([RELEASING.md](../packaging/RELEASING.md), section 2).

```bash
gh run list --workflow release --limit 1
D=$(mktemp -d) && python3 -m venv "$D" && "$D/bin/pip" install "ao-orchestrator==$V" && "$D/bin/ao" --version
```

## 7. Homebrew formula (anyone prepares; person pushes)

In `packaging/homebrew/agent-orchestrator.rb`, set `url` to the `v$V` tarball and `sha256` to the
digest below, which the release run also printed, and land it through ao with a message like the
`brew: 0.4.0` commit's. A person pushes main, copies the formula into the tap repository as
`Formula/agent-orchestrator.rb` and pushes that, then checks the install; a machine without it
installs with `brew tap hakkisagdic/tap && brew install agent-orchestrator`.

```bash
curl -sL "https://github.com/hakkisagdic/agent-orchestrator/archive/refs/tags/v$V.tar.gz" | shasum -a 256
ao verify && ao review && ao commit-ok && ao commit -m "brew: $V"
git push origin main
cp packaging/homebrew/agent-orchestrator.rb "$TAP/Formula/agent-orchestrator.rb"
git -C "$TAP" commit -am "agent-orchestrator $V" && git -C "$TAP" push
brew update && brew upgrade agent-orchestrator && brew test agent-orchestrator
```

`$TAP` is a clone of the tap repository, `homebrew-tap` under the same account.

## 8. Close the row (anyone)

Backlog #12 closes with its evidence: the tag, the PyPI release and the formula commit.
