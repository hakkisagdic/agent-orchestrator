"""What a reviewer starts on Windows ends with it (WIN-JOB).

`_stop_reviewer_leftovers` stops a process group, which Windows has no way to read. A reviewer there
that answered and exited left running whatever it had started - a server, a watcher - at work for
nobody. A job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE ends every process assigned to it, and
all they start, when the last handle closes. The reviewer starts with CREATE_SUSPENDED, is assigned
to the job and resumed before it runs a single instruction, and the job is closed in the same `finally`
that stops a group elsewhere; a job Windows refuses stops the suspended process and refuses the run.
On by default (`windows_job` in `ao features`), and turned off only on a person's record with
`ao features off windows_job --by <name>`.
"""
import ctypes
import json
import os
import queue
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ao import acp, allowlist, cli, features as F, procs
from ao import settings as S


class _JobKernel32:
    """CreateJobObjectW, SetInformationJobObject, OpenProcess and friends, as Windows answers them."""

    def __init__(self, job=7, process=9, snapshot=11, thread=13, threads=((100, 200), (4242, 8484)),
                 create_error=87, set_error=5, open_error=5, assign_error=87,
                 snapshot_error=8, thread_error=5, resume_error=6, resume_count=1,
                 make_job=True, set_info=True, open_process=True, assign=True,
                 make_snapshot=True, open_thread=True, resume_thread=True, create_process=True):
        self.job, self.process, self.snapshot, self.thread, self.error = job, process, snapshot, thread, 0
        self.threads, self._thread_index = list(threads), 0
        self.create_error, self.set_error = create_error, set_error
        self.open_error, self.assign_error = open_error, assign_error
        self.snapshot_error, self.thread_error, self.resume_error = snapshot_error, thread_error, resume_error
        self.resume_count = resume_count
        self.make_job, self.set_info, self.open_process, self.assign = make_job, set_info, open_process, assign
        self.make_snapshot, self.open_thread, self.resume_thread = make_snapshot, open_thread, resume_thread
        self.create_process = create_process
        self.created, self.opened, self.assigned, self.closed = [], [], [], []
        self.snapshots, self.opened_threads, self.resumed = [], [], []
        self.set_calls, self.limit_flags = [], None
        self.attr_updates, self.attr_deleted, self.spawned_processes, self.env_blocks = [], 0, [], []
        self.live_pids, self.job_pids = set(), []

    def CreateJobObjectW(self, attributes, name):
        self.created.append((attributes, name))
        if not self.make_job:
            self.error = self.create_error
            return 0
        return self.job

    def SetInformationJobObject(self, job, kind, info, size):
        self.set_calls.append((job, kind, size))
        self.limit_flags = info._obj.BasicLimitInformation.LimitFlags    # byref()'s object
        if not self.set_info:
            self.error = self.set_error
            return 0
        return 1

    def InitializeProcThreadAttributeList(self, buf, count, flags, size):
        size._obj.value = 64
        return 0 if buf is None else 1

    def UpdateProcThreadAttribute(self, buf, flags, attr, value, size, prev, ret_size):
        handles = list(ctypes.cast(value, ctypes.POINTER(ctypes.c_void_p * (size // ctypes.sizeof(ctypes.c_void_p)))).contents)
        self.attr_updates.append((attr, handles))
        return 1

    def DeleteProcThreadAttributeList(self, buf):
        self.attr_deleted += 1

    def CreateProcessW(self, app, cmd, proc_sa, thread_sa, inherit, flags, env, cwd, si_ex, pi):
        sa = thread_sa._obj
        sd = ctypes.cast(sa.lpSecurityDescriptor, ctypes.POINTER(procs._SecurityDescriptor)).contents
        acl = ctypes.cast(sd.Dacl, ctypes.POINTER(procs._OwnerRightsReadControlAcl)).contents
        if env is not None and env.value:
            ptr = ctypes.cast(env, ctypes.POINTER(ctypes.c_wchar))
            entries, i = [], 0
            while True:
                chars = []
                while ptr[i] != "\0":
                    chars.append(ptr[i])
                    i += 1
                i += 1
                if not chars:
                    break
                entries.append("".join(chars))
            self.env_blocks.append(entries)
        self.spawned_processes.append({
            "flags": flags,
            "inherit": bool(inherit),
            "sd_revision": sd.Revision,
            "sd_control": sd.Control,
            "ace_count": acl.AceCount,
            "ace_mask": acl.Mask,
            "sid_auth": tuple(acl.IdentifierAuthority),
            "sid_rid": acl.SubAuthority,
        })
        if not self.create_process:
            self.error = self.assign_error
            return 0
        self.assigned.append((self.job, self.process))
        pi._obj.hProcess = self.process
        pi._obj.hThread = self.thread
        pi._obj.dwProcessId = 4242
        pi._obj.dwThreadId = 8484
        return 1

    def OpenProcess(self, access, inherit, pid):
        self.opened.append((access, inherit, pid))
        if not self.open_process:
            self.error = self.open_error
            return 0
        return self.process

    def AssignProcessToJobObject(self, job, process):
        self.assigned.append((job, process))
        if not self.assign:
            self.error = self.assign_error
            return 0
        if self.opened and self.opened[-1][2] in self.live_pids:
            self.job_pids.append(self.opened[-1][2])
        return 1

    def CreateToolhelp32Snapshot(self, flags, pid):
        self.snapshots.append((flags, pid))
        if not self.make_snapshot:
            self.error = self.snapshot_error
            return ctypes.c_void_p(-1).value
        self._thread_index = 0
        return self.snapshot

    def _next_thread(self, entry):
        if self._thread_index >= len(self.threads):
            return 0
        owner, tid = self.threads[self._thread_index]
        self._thread_index += 1
        entry._obj.th32OwnerProcessID = owner
        entry._obj.th32ThreadID = tid
        return 1

    def Thread32First(self, snapshot, entry):
        assert snapshot == self.snapshot and entry._obj.dwSize == 28
        return self._next_thread(entry)

    def Thread32Next(self, snapshot, entry):
        assert snapshot == self.snapshot
        return self._next_thread(entry)

    def OpenThread(self, access, inherit, tid):
        self.opened_threads.append((access, inherit, tid))
        if not self.open_thread:
            self.error = self.thread_error
            return 0
        return self.thread

    def ResumeThread(self, thread):
        assert self.assigned, "process was resumed before being assigned to the job"
        self.resumed.append(thread)
        if not self.resume_thread:
            self.error = self.resume_error
            return 0xFFFFFFFF
        return self.resume_count

    def CloseHandle(self, handle):
        self.closed.append(handle)
        if handle == self.job and self.limit_flags == procs.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE and self.resumed:
            for pid in list(self.job_pids):
                for kill in (getattr(os, "killpg", None), os.kill):
                    if kill is not None:
                        try:
                            kill(pid, signal.SIGKILL)
                        except OSError:
                            pass


def _job(fake):
    return procs.JobObject(fake, lambda: fake.error)


def test_a_reviewer_tree_is_held_in_a_job_and_ends_with_it():
    fake = _JobKernel32()
    job = _job(fake)

    assert job.why is None and job.hold(4242) is None
    assert fake.created == [(None, None)] and fake.set_calls[0][:2] == (7, 9)   # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    assert fake.limit_flags == 0x2000                                            # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert fake.opened == [(0x0100 | 0x0001, False, 4242)]                       # PROCESS_SET_QUOTA, PROCESS_TERMINATE
    assert fake.assigned == [(7, 9)]
    assert fake.closed == [9]                                                    # the process handle, not the job

    job.close()
    job.close()                                                                  # closing again is harmless
    assert fake.closed == [9, 7] and job.handle is None


def test_a_job_windows_refuses_is_closed_and_says_why():
    fake = _JobKernel32(set_info=False)
    job = _job(fake)

    assert "SetInformationJobObject" in job.why and "access denied" in job.why
    assert job.handle is None and fake.closed == [7]
    assert job.hold(4242) == job.why and fake.assigned == []


def test_a_job_that_cannot_be_made_says_why():
    fake = _JobKernel32(make_job=False)
    job = _job(fake)

    assert "CreateJobObjectW" in job.why and "invalid parameter" in job.why
    assert job.handle is None and fake.closed == []


def test_a_process_this_user_may_not_hold_is_named_and_no_handle_leaks():
    refused = _JobKernel32(open_process=False)
    assert "OpenProcess for 4242" in _job(refused).hold(4242) and refused.closed == []

    unassignable = _JobKernel32(assign=False)
    job = _job(unassignable)
    assert "AssignProcessToJobObject for 4242" in job.hold(4242)
    assert unassignable.closed == [9]                                           # the process handle is closed anyway
    job.close()
    assert unassignable.closed == [9, 7]


@pytest.mark.skipif(sys.platform == "win32", reason="the non-Windows branch is what is asked")
def test_where_there_is_no_windows_this_holds_nothing():
    fake = _JobKernel32()
    assert procs.hold_in_job(4242, fake, lambda: fake.error) == (None, None)
    assert fake.created == [] and fake.opened == []


def test_where_windows_refuses_the_caller_is_told_and_gets_no_job(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    fake = _JobKernel32(assign=False)
    job, why = procs.hold_in_job(4242, fake, lambda: fake.error)
    assert job is None and "AssignProcessToJobObject" in why and fake.closed == [9, 7]


def test_a_windows_reviewer_is_held_by_default(project, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    held, asked = object(), []
    monkeypatch.setattr(procs, "hold_in_job", lambda pid: (asked.append(pid) or held, None))

    assert cli._reviewer_job(4242, project["root"]) == (held, None) and asked == [4242]


def test_a_reviewer_whose_job_windows_refuses_is_refused_and_says_so(project, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(procs, "hold_in_job", lambda pid: (None, "AssignProcessToJobObject for 4242 failed"))

    job, why = cli._reviewer_job(4242, project["root"])
    assert job is None and "AssignProcessToJobObject for 4242 failed" in why
    said = capsys.readouterr().out
    assert "could not be held in a Windows job" in said and "ao features off windows_job --by <name>" in said


def test_the_job_is_on_by_default_and_turns_off_only_on_a_persons_record(project, monkeypatch):
    root = project["root"]
    assert "review.windows_job" not in S.SETTINGS and "review.windows_job" not in S.CHOICES
    assert "windows_job" in F.RECORDED and F.FEATURES["windows_job"][1] is True
    assert F.enabled(cli.A.load_config(root), "windows_job") is True

    # No agent grant may admit `ao features off windows_job`.
    for rule in ("Bash(ao features:*)", "Bash(ao:*)"):
        found = allowlist.problems(["claude", "-p", "{prompt}", "--allowedTools", f"Read,{rule}"], {})
        assert ("switches the Windows reviewer job off", "ao features off windows_job --by p", rule) in found

    # Editing `.ao/config.json` alone — either via features.windows_job or a leftover review.windows_job key —
    # leaves the Windows job active because there is no person's opt-in record in the ledger.
    F.set_switch(root, "windows_job", False)
    cfg = cli.A.load_config(root)
    cfg["review"] = {"windows_job": "off"}
    cli.A.write_project_config(root, json.dumps({k: v for k, v in cfg.items() if k != "root"}))
    assert F.unrecorded(cli.A.load_config(root)) == ["windows_job"]
    assert F.enabled(cli.A.load_config(root), "windows_job") is True

    monkeypatch.setattr(sys, "platform", "win32")
    held, asked = object(), []
    monkeypatch.setattr(procs, "hold_in_job", lambda pid: (asked.append(pid) or held, None))
    assert cli._reviewer_job(4242, root) == (held, None) and asked == [4242]

    # A person's recorded `ao features off windows_job --by Ada` turns it off; non-Windows never holds a job.
    cli.A.record_opt_in(root, "features.windows_job", False, "Ada")
    assert F.unrecorded(cli.A.load_config(root)) == []
    assert F.enabled(cli.A.load_config(root), "windows_job") is False
    monkeypatch.setattr(procs, "hold_in_job", lambda pid: pytest.fail("a reviewer was held with windows_job recorded off"))
    assert cli._reviewer_job(4242, root) == (None, None)

    cli.A.record_opt_in(root, "features.windows_job", True, "Ada")
    F.set_switch(root, "windows_job", True)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert cli._reviewer_job(4242, root) == (None, None)



def _fake_popen(monkeypatch, on_communicate):
    spawned, killed = [], []

    def popen(argv, **kwargs):
        state = {"killed": False}
        proc = SimpleNamespace(
            pid=4242, args=argv, returncode=0, stdout=None, stderr=None,
            kill=lambda: state.__setitem__("killed", True) or killed.append(4242),
            communicate=lambda timeout=None: ("", "") if state["killed"] else on_communicate(),
        )
        spawned.append((argv, kwargs))
        return proc

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    monkeypatch.setattr(cli.A, "_process_start", lambda pid, **kw: "t0")
    return spawned, killed


def test_a_windows_reviewer_and_probe_start_suspended_and_resume_only_once_in_the_job(project, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    # Even if `.ao/config.json` has an unrecorded `windows_job: false`, the reviewer and probe stay in the job.
    F.set_switch(project["root"], "windows_job", False)
    fake = _JobKernel32()
    monkeypatch.setattr(procs, "_job_kernel32", lambda: fake)
    monkeypatch.setattr(procs.ctypes, "get_last_error", lambda: fake.error, raising=False)
    seen_at_communicate = []
    spawned, killed = _fake_popen(
        monkeypatch,
        lambda: seen_at_communicate.append((list(fake.assigned), list(fake.resumed)))
        or ("VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n1.2.3\n", ""),
    )

    attempt = cli._run_reviewer(project["root"], ["rev", "prompt"], 30)
    assert attempt["ok"] is True and killed == []
    assert spawned[0][1].get("creationflags", 0) & 0x00000004                     # CREATE_SUSPENDED
    assert seen_at_communicate == [([(7, 9)], [13])]
    assert fake.snapshots == [(0x4, 0)] and fake.opened_threads == [(0x0002, False, 8484)]
    assert fake.closed == [9, 11, 13, 7]

    fake = _JobKernel32()
    monkeypatch.setattr(procs, "_job_kernel32", lambda: fake)
    seen_at_communicate.clear()
    assert cli._reviewer_binary_version(project["root"], "/bin/rev") == "1.2.3"
    assert spawned[1][1].get("creationflags", 0) & 0x00000004                     # CREATE_SUSPENDED
    assert seen_at_communicate == [([(7, 9)], [13])]
    assert fake.closed == [9, 11, 13, 7]

    for bad, text in (
        (_JobKernel32(make_snapshot=False), "CreateToolhelp32Snapshot for 4242"),
        (_JobKernel32(threads=((100, 200),)), "no thread found for 4242"),
        (_JobKernel32(open_thread=False), "OpenThread for 4242"),
        (_JobKernel32(resume_thread=False), "ResumeThread for 4242"),
    ):
        job, why = procs.hold_in_job(4242, bad, lambda: bad.error)
        assert job is None and text in why and 7 in bad.closed


def test_a_reviewer_or_probe_whose_job_windows_refuses_is_killed_and_refused(project, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    fake = _JobKernel32(assign=False)
    monkeypatch.setattr(procs, "_job_kernel32", lambda: fake)
    monkeypatch.setattr(procs.ctypes, "get_last_error", lambda: fake.error, raising=False)
    answered = []
    spawned, killed = _fake_popen(
        monkeypatch,
        lambda: answered.append(True) or ("VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n1.2.3\n", ""),
    )

    attempt = cli._run_reviewer(project["root"], ["rev", "prompt"], 30)
    assert attempt["ok"] is False and attempt["kind"] == "isolation-error" and attempt["retryable"] is False
    assert "AssignProcessToJobObject for 4242" in attempt["reason"]
    assert killed == [4242] and answered == [] and fake.resumed == []
    assert "could not be held in a Windows job" in capsys.readouterr().out

    fake = _JobKernel32(assign=False)
    monkeypatch.setattr(procs, "_job_kernel32", lambda: fake)
    assert cli._reviewer_binary_version(project["root"], "/bin/rev") == ""
    assert killed == [4242, 4242] and answered == [] and fake.resumed == []


def test_a_windows_reviewer_starts_in_the_job_with_a_locked_thread_and_refuses_an_early_resume(project, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(cli.A, "_process_start", lambda pid, **kw: "t0")
    winapi_closed = []
    winapi = SimpleNamespace(
        CreateProcess=lambda *a: pytest.fail("unhooked _winapi.CreateProcess ran"),
        CloseHandle=lambda h: winapi_closed.append(h),
    )
    monkeypatch.setattr(cli.subprocess, "_winapi", winapi, raising=False)

    def popen_via_winapi(argv, **kwargs):
        si = SimpleNamespace(dwFlags=0x100, wShowWindow=0, hStdInput=3, hStdOutput=4, hStdError=5,
                             lpAttributeList={"handle_list": [3, 4, 5]})
        hp, ht, pid, tid = cli.subprocess._winapi.CreateProcess(
            None, " ".join(argv), None, None, 1, kwargs.get("creationflags", 0),
            kwargs.get("env"), kwargs.get("cwd"), si,
        )
        cli.subprocess._winapi.CloseHandle(ht)
        return SimpleNamespace(
            pid=pid, args=argv, returncode=0, stdout=None, stderr=None,
            kill=lambda: None,
            communicate=lambda timeout=None: ("VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n1.2.3\n", ""),
        )

    monkeypatch.setattr(cli.subprocess, "Popen", popen_via_winapi)
    fake = _JobKernel32()
    monkeypatch.setattr(procs, "_job_kernel32", lambda: fake)
    monkeypatch.setattr(procs.ctypes, "get_last_error", lambda: fake.error, raising=False)

    attempt = cli._run_reviewer(project["root"], ["rev", "prompt"], 30)
    assert attempt["ok"] is True
    assert fake.attr_updates == [(0x0002000D, [7]), (0x00020002, [3, 4, 5])]
    assert fake.attr_deleted == 1
    assert fake.spawned_processes == [{
        "flags": 0x00000004 | 0x00080000 | 0x00000400,
        "inherit": True,
        "sd_revision": 1,
        "sd_control": 0x0004,
        "ace_count": 1,
        "ace_mask": 0x00020000,                  # READ_CONTROL only: neither THREAD_SUSPEND_RESUME nor WRITE_DAC
        "sid_auth": (0, 0, 0, 0, 0, 3),          # SECURITY_CREATOR_SID_AUTHORITY (S-1-3-4 OWNER_RIGHTS)
        "sid_rid": 4,
    }]
    assert fake.opened == [] and fake.opened_threads == [] and winapi_closed == []
    assert fake.resumed == [13] and fake.closed == [13, 7]

    fake = _JobKernel32(create_process=False)
    monkeypatch.setattr(procs, "_job_kernel32", lambda: fake)
    attempt = cli._run_reviewer(project["root"], ["rev", "prompt"], 30)
    assert attempt["ok"] is False and attempt["kind"] == "isolation-error"
    assert "CreateProcessW failed" in attempt["reason"] and fake.closed == [7]

    # Even on the post-Popen path, a thread another process already resumed (suspend count 0) or a second thread
    # started before resume is refused and its tree is stopped.
    stopped_trees = []
    monkeypatch.setattr(cli, "_stop_reviewer", lambda pid: stopped_trees.append(pid) or [])
    for bad, text in (
        (_JobKernel32(resume_count=0), "suspend count 0, expected 1"),
        (_JobKernel32(threads=((4242, 8484), (4242, 8485))), "already has 2 threads"),
    ):
        monkeypatch.setattr(procs, "_job_kernel32", lambda b=bad: b)
        monkeypatch.setattr(procs.ctypes, "get_last_error", lambda b=bad: b.error, raising=False)
        _, killed = _fake_popen(
            monkeypatch,
            lambda: ("VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n", ""),
        )
        attempt = cli._run_reviewer(project["root"], ["rev", "prompt"], 30)
        assert attempt["ok"] is False and attempt["kind"] == "isolation-error"
        assert text in attempt["reason"] and killed == [4242] and 7 in bad.closed
    assert stopped_trees == [4242, 4242]


def test_create_process_sorts_the_unicode_environment_block_case_insensitively():
    fake = _JobKernel32()
    job = _job(fake)
    si = SimpleNamespace(dwFlags=0, wShowWindow=0, hStdInput=0, hStdOutput=0, hStdError=0, lpAttributeList=None)
    env = {
        "z_last": "1",
        "Path": r"C:\Windows\system32",
        "b_lower": "2",
        "A_first": "3",
        "GIT_CEILING_DIRECTORIES": r"C:\Temp",
        "PWD": r"C:\Temp",
    }
    job._create_process(None, "rev prompt", None, None, 0, procs.CREATE_SUSPENDED, env, None, si)
    assert fake.env_blocks == [[
        "A_first=3",
        "b_lower=2",
        r"GIT_CEILING_DIRECTORIES=C:\Temp",
        r"Path=C:\Windows\system32",
        r"PWD=C:\Temp",
        "z_last=1",
    ]]
    job.close()


def _fake_acp_popen(monkeypatch, fake_box, via_winapi=True):
    spawned, killed, seen_at_send = [], [], []

    def popen(argv, **kwargs):
        pid = 4242
        if via_winapi:
            si = SimpleNamespace(dwFlags=0x100, wShowWindow=0, hStdInput=3, hStdOutput=4, hStdError=5,
                                 lpAttributeList={"handle_list": [3, 4, 5]})
            _, ht, pid, _ = cli.subprocess._winapi.CreateProcess(
                None, " ".join(argv), None, None, 1, kwargs.get("creationflags", 0),
                kwargs.get("env"), kwargs.get("cwd"), si,
            )
            cli.subprocess._winapi.CloseHandle(ht)
        lines = queue.Queue()
        state = {"killed": False}

        def write(raw):
            msg = json.loads(raw)
            method, mid = msg.get("method"), msg.get("id")
            if method == "initialize":
                seen_at_send.append((list(fake_box[0].assigned), list(fake_box[0].resumed)))
                lines.put(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {
                    "protocolVersion": 1, "agentCapabilities": {},
                    "agentInfo": {"name": "fake-acp", "version": "1.0"}}}))
            elif method == "session/new":
                lines.put(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "s1"}}))
            elif method == "session/prompt":
                lines.put(json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": "s1", "update": {"sessionUpdate": "agent_message_chunk", "content": {
                        "type": "text", "text": "VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n"}}}}))
                lines.put(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}}))
                lines.put(None)

        stdin = SimpleNamespace(write=write, flush=lambda: None, close=lambda: None)
        stdout = SimpleNamespace(
            readline=lambda: "" if state["killed"] else (lines.get() or ""),
            close=lambda: lines.put(None),
        )
        proc = SimpleNamespace(
            pid=pid, args=argv, returncode=0, stdin=stdin, stdout=stdout, stderr=None,
            poll=lambda: -9 if state["killed"] else (0 if seen_at_send else None),
            wait=lambda timeout=None: 0,
            kill=lambda: state.__setitem__("killed", True) or killed.append(pid) or lines.put(None),
        )
        spawned.append((argv, kwargs))
        return proc

    monkeypatch.setattr(acp.subprocess, "Popen", popen)
    return spawned, killed, seen_at_send


def test_an_acp_reviewer_on_windows_starts_in_the_job_and_is_refused_when_the_job_is(project, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(cli.A, "_process_start", lambda pid, **kw: "t0")
    winapi_closed = []
    winapi = SimpleNamespace(
        CreateProcess=lambda *a: pytest.fail("unhooked _winapi.CreateProcess ran"),
        CloseHandle=lambda h: winapi_closed.append(h),
    )
    monkeypatch.setattr(cli.subprocess, "_winapi", winapi, raising=False)

    fake_box = [_JobKernel32()]
    monkeypatch.setattr(procs, "_job_kernel32", lambda: fake_box[0])
    monkeypatch.setattr(procs.ctypes, "get_last_error", lambda: fake_box[0].error, raising=False)
    spawned, killed, seen_at_send = _fake_acp_popen(monkeypatch, fake_box, via_winapi=True)

    attempt = cli._run_acp_reviewer(project["root"], ["rev", "--acp"], "qoder", "review this", 30, "qoder-reviewer")
    assert attempt["ok"] is True and attempt["transport"] == "acp" and killed == []
    assert spawned[0][1].get("creationflags", 0) & procs.CREATE_SUSPENDED
    assert seen_at_send == [([(7, 9)], [13])]
    assert fake_box[0].attr_updates == [(0x0002000D, [7]), (0x00020002, [3, 4, 5])]
    assert fake_box[0].spawned_processes[0]["ace_mask"] == 0x00020000
    assert fake_box[0].resumed == [13] and fake_box[0].closed == [13, 7] and winapi_closed == []

    fake_box[0] = _JobKernel32(create_process=False)
    attempt = cli._run_acp_reviewer(project["root"], ["rev", "--acp"], "qoder", "review this", 30, "qoder-reviewer")
    assert attempt["ok"] is False and attempt["kind"] == "isolation-error"
    assert "CreateProcessW failed" in attempt["reason"] and fake_box[0].closed == [7]

    fake_box[0] = _JobKernel32(assign=False)
    spawned, killed, seen_at_send = _fake_acp_popen(monkeypatch, fake_box, via_winapi=False)
    attempt = cli._run_acp_reviewer(project["root"], ["rev", "--acp"], "qoder", "review this", 30, "qoder-reviewer")
    assert attempt["ok"] is False and attempt["kind"] == "isolation-error" and attempt["retryable"] is False
    assert "AssignProcessToJobObject for 4242" in attempt["reason"]
    assert killed == [4242] and seen_at_send == [] and fake_box[0].resumed == [] and 7 in fake_box[0].closed
    assert "could not be held in a Windows job" in capsys.readouterr().out

    stopped_trees = []
    monkeypatch.setattr(cli.A, "kill_turn", lambda pid, sig: stopped_trees.append(pid) or [])
    for bad, text in (
        (_JobKernel32(resume_count=0), "suspend count 0, expected 1"),
        (_JobKernel32(threads=((4242, 8484), (4242, 8485))), "already has 2 threads"),
    ):
        fake_box[0] = bad
        _, killed, seen_at_send = _fake_acp_popen(monkeypatch, fake_box, via_winapi=False)
        attempt = cli._run_acp_reviewer(project["root"], ["rev", "--acp"], "qoder", "review this", 30, "qoder-reviewer")
        assert attempt["ok"] is False and attempt["kind"] == "isolation-error"
        assert text in attempt["reason"] and seen_at_send == [] and 7 in bad.closed
    assert stopped_trees == [4242, 4242]


REAL_WINDOWS = sys.platform == "win32"

SPAWN_TREE = """
import os, subprocess, sys
mid = (
    "import os, subprocess, sys; "
    "flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) if os.name == 'nt' else 0; "
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
    "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags); "
    "open(sys.argv[1], 'w', encoding='utf-8').write(str(p.pid))"
)
subprocess.run([sys.executable, "-c", mid, sys.argv[1]], check=True)
print("VERDICT: APPROVED\\nBLOCKER: 0\\nHIGH: 0\\nMEDIUM: 0\\nLOW: 0")
"""

ACP_SPAWN_TREE = """
import json, subprocess, sys
mid = (
    "import os, subprocess, sys; "
    "flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) if os.name == 'nt' else 0; "
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
    "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags); "
    "open(sys.argv[1], 'w', encoding='utf-8').write(str(p.pid))"
)
subprocess.run([sys.executable, "-c", mid, sys.argv[1]], check=True)
def say(msg):
    print(json.dumps(msg), flush=True)
while True:
    line = sys.stdin.readline()
    if not line:
        break
    msg = json.loads(line)
    method, mid_id = msg.get("method"), msg.get("id")
    if method == "initialize":
        say({"jsonrpc": "2.0", "id": mid_id, "result": {"protocolVersion": 1, "agentCapabilities": {},
                                                        "agentInfo": {"name": "tree-acp", "version": "1.0"}}})
    elif method == "session/new":
        say({"jsonrpc": "2.0", "id": mid_id, "result": {"sessionId": "s1"}})
    elif method == "session/prompt":
        say({"jsonrpc": "2.0", "method": "session/update",
             "params": {"sessionId": "s1", "update": {"sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "VERDICT: APPROVED\\nBLOCKER: 0\\nHIGH: 0\\nMEDIUM: 0\\nLOW: 0\\n"}}}})
        say({"jsonrpc": "2.0", "id": mid_id, "result": {"stopReason": "end_turn"}})
        break
"""


def _child_alive(pid):
    if REAL_WINDOWS:
        return bool(procs._Windows.alive(pid))
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return not procs.zombie(pid)


def _wait_gone(pid, timeout=1.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _child_alive(pid):
            return True
        time.sleep(0.05)
    return not _child_alive(pid)


def test_closing_a_reviewer_job_ends_a_real_child_process_tree(project, monkeypatch, tmp_path):
    real_popen = subprocess.Popen
    fake_box = None
    if not REAL_WINDOWS:
        monkeypatch.setattr(procs, "_NATIVE", procs._NATIVE)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(cli.A, "_process_start", lambda pid, **kw: "t0")
        monkeypatch.setattr(cli, "_stop_reviewer_leftovers", lambda proc, label: [])
        monkeypatch.setattr(cli.A, "kill_turn", lambda pid, sig: [])
        fake_box = [_JobKernel32()]
        monkeypatch.setattr(procs, "_job_kernel32", lambda: fake_box[0])
        monkeypatch.setattr(procs.ctypes, "get_last_error", lambda: fake_box[0].error, raising=False)

        def popen_in_job(argv, **kwargs):
            flags = kwargs.pop("creationflags", 0)
            assert flags & procs.CREATE_SUSPENDED
            kwargs["start_new_session"] = True
            proc = real_popen(argv, **kwargs)
            if hasattr(fake_box[0], "live_pids"):
                fake_box[0].live_pids.add(proc.pid)
            fake_box[0].threads = [(proc.pid, proc.pid + 1000)]
            return proc

        monkeypatch.setattr(cli.subprocess, "Popen", popen_in_job)

    cleanup_pids = []
    try:
        # 1. Direct job lifecycle: while the job handle stays open after the parent exits, the detached grandchild
        # lives on; closing the job handle terminates it.
        direct_pid_file = tmp_path / "direct_child.pid"
        with procs.creating_in_job(True) as pre_job:
            proc = subprocess.Popen(
                [sys.executable, "-c", SPAWN_TREE, str(direct_pid_file)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", **cli._reviewer_group(suspended=True),
            )
            cleanup_pids.append(proc.pid)
            if REAL_WINDOWS:
                # Before hold_in_job resumes the primary thread, another handle opening it with THREAD_SUSPEND_RESUME
                # is refused by the OWNER_RIGHTS READ_CONTROL-only DACL.
                k32 = procs._job_kernel32()
                snap = k32.CreateToolhelp32Snapshot(0x00000004, 0)
                entry = procs._ThreadEntry32(dwSize=ctypes.sizeof(procs._ThreadEntry32))
                tids = []
                if k32.Thread32First(snap, ctypes.byref(entry)):
                    while True:
                        if entry.th32OwnerProcessID == proc.pid:
                            tids.append(entry.th32ThreadID)
                        if not k32.Thread32Next(snap, ctypes.byref(entry)):
                            break
                k32.CloseHandle(snap)
                assert len(tids) == 1
                assert not k32.OpenThread(0x0002, False, tids[0])
            job, why = procs.hold_in_job(proc.pid)
            assert why is None and job is not None and pre_job is job
        proc.communicate(timeout=15)
        assert proc.returncode == 0
        direct_child = int(direct_pid_file.read_text(encoding="utf-8").strip())
        cleanup_pids.append(direct_child)
        assert _child_alive(direct_child), "detached grandchild should survive its parent while the job stays open"
        job.close()
        assert _wait_gone(direct_child), "closing the job must terminate the detached grandchild"

        # 2. Spawned reviewer lifecycle: _run_reviewer closes the job in finally and ends the orphaned grandchild.
        if fake_box is not None:
            fake_box[0] = _JobKernel32()
        spawn_pid_file = tmp_path / "spawn_child.pid"
        attempt = cli._run_reviewer(project["root"], [sys.executable, "-c", SPAWN_TREE, str(spawn_pid_file)], 30)
        assert attempt["ok"] is True
        spawn_child = int(spawn_pid_file.read_text(encoding="utf-8").strip())
        cleanup_pids.append(spawn_child)
        assert _wait_gone(spawn_child), "_run_reviewer must close the job and end the reviewer's child tree"

        # 3. ACP reviewer lifecycle: _run_acp_reviewer closes the job in finally and ends the orphaned grandchild.
        if fake_box is not None:
            fake_box[0] = _JobKernel32()
        acp_pid_file = tmp_path / "acp_child.pid"
        attempt = cli._run_acp_reviewer(
            project["root"], [sys.executable, "-c", ACP_SPAWN_TREE, str(acp_pid_file)],
            "qoder", "review this", 30, "qoder-reviewer",
        )
        assert attempt["ok"] is True and attempt["transport"] == "acp"
        acp_child = int(acp_pid_file.read_text(encoding="utf-8").strip())
        cleanup_pids.append(acp_child)
        assert _wait_gone(acp_child), "_run_acp_reviewer must close the job and end the ACP reviewer's child tree"
    finally:
        for pid in cleanup_pids:
            for fn in (getattr(os, "killpg", None), os.kill):
                try:
                    if fn is not None and not REAL_WINDOWS:
                        fn(pid, signal.SIGKILL)
                except OSError:
                    pass
            if REAL_WINDOWS and _child_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
