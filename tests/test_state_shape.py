"""The watchdog's state reads as a fresh one when its file holds no JSON object (STATE-SHAPE).

The retrospective review of IDLE-ANSWER found the MCP status tool reading `W.load_state(root).get(...)`
where `ao status` guarded the same read. A state file of `null`, a list or a string - it is in ~/.ao,
which an agent can write - stopped the status tool and the cycle on their first `.get`. Every reader
asks the state for keys, so `load_state` answers a dict.
"""
import os

import pytest

from ao import mcp, watchdog as W

FRESH = {"attempts": 0, "last_nudge": 0, "last_size": 0}


def _state_file(root, content):
    path = W.state_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


@pytest.mark.parametrize("content", ["null", "[1, 2]", '"text"', "7", "{not json"])
def test_a_state_file_that_holds_no_json_object_reads_as_a_fresh_state(project, content):
    _state_file(project["root"], content)

    assert W.load_state(project["root"]) == FRESH


def test_a_state_that_is_an_object_reads_as_written(project):
    _state_file(project["root"], '{"attempts": 3, "idle_answer": {"since": 5}}')

    assert W.load_state(project["root"]) == {"attempts": 3, "idle_answer": {"since": 5}}


def test_the_mcp_status_tool_answers_over_a_state_of_null(project):
    _state_file(project["root"], "null")

    assert mcp.status_payload(project)["nothing_to_do_since"] is None
