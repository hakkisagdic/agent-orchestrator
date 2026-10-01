"""The test files a push touches, which the pre-push hook runs before the whole suite.

Git hands the hook one line per ref on standard input, `<local ref> <local sha> <remote ref> <remote
sha>`; this reads them and prints a test file per line. A changed test file is itself. A changed
source file brings each test file that names a function or class its diff touches. A change to the
suite's own conftest.py brings nothing, since every test may move with it, and so does a selection
past CAP files, which would take as long as the suite. What is printed only decides what runs first:
the whole suite runs after it whatever this prints, so a guess here can delay a red, never hide one.
"""
import os
import re
import subprocess
import sys

CAP = 60
DOCS = ("README.md", "README.tr.md", "CHANGELOG.md")
TOUCHED = re.compile(r"^[+-]\s*(?:async\s+)?(?:def|class)\s+(\w+)")
HEADER = re.compile(r"^@@.*@@\s*(?:async\s+)?(?:def|class)\s+(\w+)")


def git(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.stdout if result.returncode == 0 else ""


def ranges(lines):
    """(base, tip) for each ref pushed; a deleted ref pushes nothing, and a new one is measured from main."""
    for line in lines:
        fields = line.split()
        if len(fields) != 4 or not fields[1].strip("0"):
            continue
        tip, remote = fields[1], fields[3]
        base = remote if remote.strip("0") else (git("merge-base", tip, "origin/HEAD").strip()
                                                 or git("merge-base", tip, "origin/main").strip())
        if base:
            yield base, tip


def names_touched(base, tip, path):
    """The functions and classes a source file's diff touches: the one each hunk is in, and each added or removed."""
    names = set()
    for line in git("diff", "-U0", f"{base}..{tip}", "--", path).splitlines():
        found = HEADER.match(line) or TOUCHED.match(line)
        if found and not found.group(1).startswith("__"):
            names.add(found.group(1))
    return names


def main():
    tests = sorted(name for name in os.listdir("tests") if name.startswith("test_") and name.endswith(".py"))
    chosen, names = set(), set()
    for base, tip in ranges(sys.stdin.read().splitlines()):
        for path in git("diff", "--name-only", "--diff-filter=d", f"{base}..{tip}").splitlines():
            if path == "tests/conftest.py":
                return 0
            if path.startswith("tests/") and os.path.basename(path) in tests:
                chosen.add(path)
            elif path.startswith("src/") and path.endswith(".py"):
                names |= names_touched(base, tip, path)
            elif path.startswith("docs/") or path in DOCS:
                chosen.update(f"tests/{name}" for name in tests if name.startswith("test_docs_"))
    if names:
        pattern = re.compile(r"\b(?:%s)\b" % "|".join(sorted(map(re.escape, names))))
        for name in tests:
            with open(os.path.join("tests", name), encoding="utf-8", errors="replace") as fh:
                if pattern.search(fh.read()):
                    chosen.add(f"tests/{name}")
    if len(chosen) <= CAP:
        print("\n".join(sorted(chosen)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
