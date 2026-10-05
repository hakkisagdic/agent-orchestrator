"""The README's adapter table, in both languages, is what the packaged adapters declare (README-ADAPTERS).

The release checklist asks for it by hand, and 0.5.0 was prepared with a table that still named codex and qoder
untested, which their adapters had moved to partial, and left out twelve adapters ao ships.
"""
import os
import re

from ao import lib as A

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROW = re.compile(r"^\| \*\*(full|partial|documented|untested)\*\* — [^|]*\| ([^|]*) \|$", re.M)


def test_the_readme_adapter_table_names_each_adapter_as_its_adapter_declares_it():
    declared = {}
    for name, adapter in A.package_adapters().items():
        if adapter.get("kind") != "cloud":          # as `ao adapters` lists them: a cloud agent's is no harness here
            declared.setdefault(adapter.get("verified"), set()).add(name)

    for readme in ("README.md", "README.tr.md"):
        with open(os.path.join(ROOT, readme), encoding="utf-8") as fh:
            table = {level: {name.strip() for name in names.split(",") if name.strip()}
                     for level, names in ROW.findall(fh.read())}
        assert table == {level: names for level, names in declared.items() if level in table}, readme
        assert set(table) == set(declared), readme
