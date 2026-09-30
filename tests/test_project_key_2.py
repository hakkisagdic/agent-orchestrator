"""A project whose name the registry could not record takes its path's own name (PROJECT-KEY-2).

The retrospective review of PROJECT-KEY found project_key falling back to the bare basename when the
registry could not be locked or written: two new projects called `api` both answered `api`, and a push
window allowed in one opened the other's.
"""
from ao import lib as A, storage
from tests.test_project_key import _project, home  # noqa: F401  (the fixture)


def test_two_projects_of_one_name_whose_registry_cannot_be_written_do_not_share_it(tmp_path, home, monkeypatch):
    def refuse(path, timeout=10.0):
        raise OSError("the registry cannot be locked")

    monkeypatch.setattr(storage, "_exclusive_lock", refuse)
    keys = {A.project_key(_project(tmp_path / "a")), A.project_key(_project(tmp_path / "b"))}

    assert len(keys) == 2 and all(key.startswith("api-") for key in keys)


def test_a_name_the_registry_recorded_stands_when_only_the_projects_own_mark_cannot_be_written(
        tmp_path, home, monkeypatch):
    real = storage.replace_file_durably

    def replace(path, data, *args, **kwargs):
        if path.endswith(A.PROJECT_KEY_FILE):
            raise OSError("the project's .ao is read-only")
        return real(path, data, *args, **kwargs)

    monkeypatch.setattr(storage, "replace_file_durably", replace)
    root = _project(tmp_path)

    assert A.project_key(root) == "api" and A.project_key(root) == "api"
