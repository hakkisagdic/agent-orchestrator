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

```bash
python3 -m build            # wheel + sdist into dist/
python3 -m twine upload dist/*
```

Then `pip install agent-orchestrator`, `uv tool install agent-orchestrator`, or
`pipx install agent-orchestrator` all work.

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
