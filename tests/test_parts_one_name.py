"""A name is bound once in the namespace a module's parts load into (PARTS-ONE-NAME).

lib.py and cli.py each run their parts in their own globals (`_part(name, globals())`), so a module and its parts
are one namespace written in a dozen files, and a name bound at the top level of two of them is one name: the
file that runs later replaces what the earlier one bound, for every function of both. Each file reads right
alone, and nothing fails as it loads. `_inside` was written in lib_adapters.py while lib_transcript.py, which
runs after it, held another function of that name - links not followed, the folder itself outside - and that
stricter reading answered every caller of the first (PYTHON-ENV). `_OBJECT_ID` was compiled in two parts, the
same pattern twice, and stayed the same only while nobody changed one of them.

So the files of each namespace are read as the loader runs them - the module, each part a top-level
`_part(name, globals())` call loads, a part's own parts - and a name is bound at their top level by one
definition, or by one import however many of them write it. Every file in the parts folder is some module's
part, so none stands outside what is read here.
"""
import ast
import os

from ao import lib as A

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "ao")
MODULES = ("lib.py", "cli.py")


def _read(rel):
    with open(os.path.join(SRC, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _files(module, read=_read):
    """The module and every part that loads into its namespace, in the order the loader meets them."""
    found, waiting = [], [module]
    while waiting:
        rel = waiting.pop(0)
        if rel in found:
            continue
        found.append(rel)
        waiting += [A._part_file(rel, name) for name in A.top_level_statements(read(rel), rel)[2]]
    return found


def _bindings(source, rel):
    """[(line, name, what binds it, is it a def or a class)] at the top level of one file, in the order they run:
    "a definition at file:line" for a def, a class or an assignment, the import itself for an import - the same
    import written in two files binds one thing."""
    bound = []
    for node in A._module_level(ast.parse(source, rel).body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [(node.name, None)]
        elif isinstance(node, ast.Import):
            names = [(alias.asname or alias.name.split(".")[0], f"import {alias.name}") for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(alias.asname or alias.name, f"from {'.' * node.level}{node.module or ''} import {alias.name}")
                     for alias in node.names]
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names = [(node.id, None)]
        else:
            continue
        bound += [(node.lineno, name, imported or f"a definition at {rel}:{node.lineno}", not isinstance(node, ast.Name)
                   and imported is None) for name, imported in names]
    return sorted(bound)


def _collisions(module, read=_read):
    """{name: [what binds it, as the loader meets each]} for every name two files of one namespace bind to two
    things, and every def or class one file writes twice. A name one file binds again by an assignment - a
    fallback beside an import, a table added to - is read where it is written, in that file, and is none."""
    bound, twice = {}, set()
    for rel in _files(module, read):
        defined = set()
        for _, name, what, definition in _bindings(read(rel), rel):
            bound.setdefault(name, {}).setdefault(what, set()).add(rel)
            if definition and name in defined:
                twice.add(name)
            defined |= {name} if definition else set()
    return {name: list(how) for name, how in bound.items()
            if name in twice or (len(how) > 1 and len(set().union(*how.values())) > 1)}


# ---- the reading itself, on namespaces written here ----------------------------------------------------------

def _namespace(**files):
    return lambda rel: files[rel.replace("/", "_").replace(".py", "")]


def test_a_name_two_parts_define_is_found_with_both_places():
    read = _namespace(lib='_part("lib_one", globals())\n_part("lib_two", globals())\n',
                      parts_lib_one="def _inside(path, top):\n    return True\n",
                      parts_lib_two="import os\n\n\ndef _inside(path, directory):\n    return False\n")

    assert _collisions("lib.py", read) == {"_inside": ["a definition at parts/lib_one.py:1",
                                                       "a definition at parts/lib_two.py:4"]}


def test_a_part_s_own_part_and_the_module_itself_are_the_same_namespace():
    read = _namespace(lib='LIMIT = 1\n_part("lib_one", globals())\n',
                      parts_lib_one='_part("lib_two", globals())\n',
                      parts_lib_two="if True:\n    LIMIT: int = 2\n")

    assert _collisions("lib.py", read) == {"LIMIT": ["a definition at lib.py:1", "a definition at parts/lib_two.py:2"]}


def test_one_import_written_in_every_part_is_one_binding_and_two_imports_under_a_name_are_not():
    same = _namespace(lib='import os\n_part("lib_one", globals())\n', parts_lib_one="import os\nfrom . import settings as S\n")
    other = _namespace(lib='from . import settings as S\n_part("lib_one", globals())\n',
                       parts_lib_one="from . import features as S\n")
    defined = _namespace(lib='import json\n_part("lib_one", globals())\n', parts_lib_one="def json():\n    pass\n")

    assert _collisions("lib.py", same) == {}
    assert _collisions("lib.py", other) == {"S": ["from . import settings", "from . import features"]}
    assert _collisions("lib.py", defined) == {"json": ["import json", "a definition at parts/lib_one.py:1"]}


def test_a_def_written_twice_in_one_file_is_found_and_what_a_file_binds_again_by_assignment_is_not():
    read = _namespace(lib="try:\n    import fcntl\nexcept ImportError:\n    fcntl = None\nTABLE = (1,)\nTABLE += (2,)\n\n\n"
                          "def once():\n    value = 1\n    value = 2\n    return value\n\n\n"
                          "def twice():\n    pass\n\n\ndef twice():\n    pass\n")

    assert _collisions("lib.py", read) == {"twice": ["a definition at lib.py:15", "a definition at lib.py:19"]}


def test_a_fallback_one_file_binds_is_replaced_by_another_file_s_plain_import_and_is_found():
    read = _namespace(lib='try:\n    import fcntl\nexcept ImportError:\n    fcntl = None\n_part("lib_one", globals())\n',
                      parts_lib_one="import fcntl\n")

    assert _collisions("lib.py", read) == {"fcntl": ["import fcntl", "a definition at lib.py:4"]}


# ---- ao's own ---------------------------------------------------------------------------------------------

def test_every_file_in_the_parts_folder_is_a_part_of_one_module():
    loaded = [rel for module in MODULES for rel in _files(module) if rel != module]
    there = sorted(f"parts/{name}" for name in os.listdir(os.path.join(SRC, "parts"))
                   if name.endswith(".py") and name != "__init__.py")

    assert sorted(loaded) == there


def test_no_name_is_bound_twice_in_a_namespace_ao_s_parts_load_into():
    found = {module: _collisions(module) for module in MODULES}

    assert found == {module: {} for module in MODULES}, \
        "a part loaded later replaces what an earlier one bound under the same name; name one of them apart"
