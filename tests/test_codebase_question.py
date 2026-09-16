import json
import os
import sys
from types import SimpleNamespace

from ao import cli, lib as A

CITED = ("import json, sys\n"
         "print(json.dumps({'answer': 'the journal is written in src/a.py before admission: ' + sys.argv[1],\n"
         "                  'citations': [{'file': 'src/a.py', 'lines': '1-2'}]}))\n")
UNCITED = "import json\nprint(json.dumps({'answer': 'trust me', 'citations': []}))\n"


def _ask(cfg, question="where is the journal written?"):
    return cli.cmd_ask(cfg, SimpleNamespace(question=question, options=[], context=None, slice=None, codebase=True))


def test_a_codebase_question_is_answered_with_its_citations_or_not_at_all(project, capsys):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"))
    open(os.path.join(root, "src", "a.py"), "w").write("x = 1\ny = 2\n")

    assert _ask(project) == 2
    assert "no codebase provider is configured" in capsys.readouterr().out

    cited = dict(project, codebase={"provider": {"argv": [sys.executable, "-c", CITED, "{question}"]}})
    assert _ask(cited) == 0
    out = capsys.readouterr().out
    assert "where is the journal written?" in out and "src/a.py:1-2" in out

    uncited = dict(project, codebase={"provider": {"argv": [sys.executable, "-c", UNCITED]}})
    assert _ask(uncited) == 2
    assert "cites no file and line range" in capsys.readouterr().out
    assert A.decisions(root) == []
