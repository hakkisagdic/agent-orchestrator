import json
import os
import subprocess
import sys
from types import SimpleNamespace

from ao import cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE = r"""
import json, os, sys, sysconfig
before = set(sys.modules)            # what the interpreter itself loaded at start-up
sys.path.insert(0, os.path.join(sys.argv[1], "src"))
from ao import cli
for argv in (["ao", "-C", sys.argv[2], "status"], ["ao", "-C", sys.argv[2], "board"],
             ["ao", "-C", sys.argv[2], "doctor"]):
    sys.argv = argv
    try:
        cli.main()
    except SystemExit:
        pass
    except Exception:
        pass
stdlib = {os.path.realpath(sysconfig.get_paths()[k]) for k in ("stdlib", "platstdlib")}
if os.name == "nt":                   # Windows keeps the standard library's extension modules in DLLs
    stdlib.add(os.path.realpath(os.path.join(sys.base_prefix, "DLLs")))
package = os.path.realpath(os.path.join(sys.argv[1], "src", "ao"))
outside = []
for name, module in list(sys.modules.items()):
    if name in before:
        continue
    path = getattr(module, "__file__", None)
    if not path:
        continue
    real = os.path.realpath(path)
    if name == "ao" or name.startswith("ao.") or real.startswith(package + os.sep):
        continue                      # ao itself, wherever this interpreter found it
    in_stdlib = any(real.startswith(base + os.sep) for base in stdlib) and "site-packages" not in real
    if not in_stdlib:
        outside.append(name)
print(json.dumps(sorted(outside)))
"""


def test_the_core_loads_nothing_from_outside_the_standard_library(project):
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH",)}
    run = subprocess.run([sys.executable, "-I", "-c", PROBE, ROOT, project["root"]], capture_output=True,
                         text=True, env=env, timeout=120)
    lines = [line for line in run.stdout.splitlines() if line.startswith("[")]

    assert lines, run.stdout + run.stderr
    assert json.loads(lines[-1]) == []


def test_the_doctor_lists_optional_capabilities_and_how_to_enable_them(project, monkeypatch):
    from ao import email, telegram
    monkeypatch.setattr(email, "config", lambda: None)
    monkeypatch.setattr(telegram, "config", lambda: {"chats": [1]})
    monkeypatch.setattr(cli.shutil, "which", lambda name, *args, **kwargs: None)

    features = dict((name, (state, hint)) for name, state, hint in cli._optional_features(project))

    assert features["telegram"][0] == "configured" and features["email"] == ("absent", "ao email setup")
    assert features["keyflip"][0] == "absent" and features["ping"][0] == "absent"
