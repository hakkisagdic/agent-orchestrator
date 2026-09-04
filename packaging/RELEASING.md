# Releasing

Three channels, one source of truth: the version in `pyproject.toml`.

## 1. Tag

```bash
V=0.1.0
sed -i '' "s/^version = .*/version = \"$V\"/" pyproject.toml
sed -i '' "s/^__version__ = .*/__version__ = \"$V\"/" src/ao/__init__.py
git commit -am "release: v$V" && git tag "v$V" && git push --follow-tags
```

## 2. PyPI

Publishing is automatic: pushing a `v*` tag runs `.github/workflows/release.yml`,
which builds, checks that the tag matches the packaged version, checks the
adapters actually shipped in the wheel, and uploads over **Trusted Publishing** —
no token in the repository, in Actions secrets, or on a laptop.

One-time setup on pypi.org, before the first tag (PyPI account owner, in a
browser — it cannot be scripted):

    pypi.org -> Your projects -> Publishing -> Add a pending publisher
      PyPI project name: ao-cli
      Owner:             hakkisagdic
      Repository:        agent-orchestrator
      Workflow:          release.yml
      Environment:       pypi

Then `pip install ao-cli`, `uv tool install ao-cli` or `pipx install ao-cli` all
work, and each provides the `ao` command.

The distribution is `ao-cli` because `agent-orchestrator` on PyPI belongs to an
unrelated project. The import package stays `ao`, which is also the command, so
nothing inside the code or the docs has to know about the difference.

Check the wheel before uploading — the failure that matters is packaged data
going missing, and nothing in a smoke test of `--help` would catch it:

```bash
python3 -c "
import zipfile, glob
n = zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist()
assert len([x for x in n if '/adapters/' in x]) >= 15, 'adapters did not ship'
print('ok:', len(n), 'entries')"
```

## 3. Homebrew

The formula lives in `packaging/homebrew/`. Copy it into a tap repository
(`homebrew-tap/Formula/agent-orchestrator.rb`) and fill in the release hash:

```bash
V=0.1.0
curl -sL "https://github.com/hakkisagdic/agent-orchestrator/archive/refs/tags/v$V.tar.gz" \
  | shasum -a 256
```

Then `brew tap hakkisagdic/tap && brew install agent-orchestrator`.

## What must keep working after any change

The git-clone install is documented in both READMEs and is what someone with only
the system Python has. It is not a legacy path — it is the zero-install promise —
so `bin/ao` and `scripts/ao-watchdog` stay as thin shims over the package, and a
release is not done until both of these pass:

```bash
./bin/ao adapters                     # clone, nothing installed
PYTHONPATH=src python3 -m ao adapters # module
ao adapters                           # installed console script
```

Existing launchd jobs name `scripts/ao-watchdog` by absolute path. Removing that
file would silently stop watching every project that already depends on it.
