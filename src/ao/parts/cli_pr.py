"""Pull requests: `ao pr watch --once`, which reads this checkout's pull requests and mails the implementer.

A part of src/ao/cli.py, run in its namespace by `_part` as the parts moved out of it are (#44); it is
not importable on its own (PR-WATCH).
"""


def cmd_pr(cfg, args):
    """One read of this checkout's pull requests through gh; each new fact mailed to the implementer once (PR-WATCH).

    Opt-in, because it reaches GitHub on the project's behalf; read-only there, because gh is asked
    `pr list` and `pr view` and nothing else; and one pass, because the watchdog does not run it yet.
    Exit 2 when it is off or asked to loop, 1 when gh cannot answer - nothing is mailed or recorded
    then - and 0 after a pass, whatever it found, or when it stood down for a pass already running.
    """
    if not args.once:
        print("ao pr watch runs one pass and exits: `ao pr watch --once`. It does not loop on its own; "
              "running it on the watchdog's cycle comes in a later slice")
        return 2
    if S.get(cfg, "pr.watch") != "on":
        print(f"{C['yellow']}pr watch is off{C['reset']} for this project, so nothing was read. "
              "`ao config set pr.watch on` turns it on: it reads this checkout's pull requests through gh, "
              "never writes to GitHub, and mails the implementer a failed check, a merge conflict or requested "
              "changes, once each")
        return 2
    gh = A.runnable_binary("gh")
    if not gh:
        print(f"{C['red']}gh is not on this machine{C['reset']} (PATH, binaries.extra_dirs, the usual install "
              "directories): ao reads pull requests only through GitHub's CLI. Install it and run `gh auth login`; "
              "nothing was mailed")
        return 1
    try:
        done = A.pr_watch(cfg["root"], cfg, gh)
    except RuntimeError as exc:        # gh could not answer, or git could not list the branches
        print(f"{C['red']}not read{C['reset']}: {exc}. Nothing was mailed")
        return 1
    if done is None:                   # as a watchdog cycle does when another one runs
        print("another pr watch pass is running for this project; standing down")
        return 0
    if done["problem"]:
        print(f"{C['yellow']}note{C['reset']}: .ao/pr-watch.json could not be read ({done['problem']}), so what "
              "stands now was mailed as new, once")
    if done["cut"]:
        print(f"{C['yellow']}note{C['reset']}: gh listed {A.PR_LIST_LIMIT} open pull requests, as many as it was "
              "asked for; one beyond them was not read, and what was mailed about it stands")
    for number, fact, head, mail in done["sent"]:
        print(f"  {C['green']}mailed{C['reset']} PR #{number} {fact} at {head[:12]}: {mail}")
    for number, fact, head in done["standing"]:
        print(f"  {C['dim']}already mailed{C['reset']} PR #{number} {fact} at {head[:12]}")
    implementer, _ = A.mail_names(cfg)
    print(f"read {done['read']} open pull request(s) of this checkout's branches: {len(done['sent'])} new "
          f"fact(s) mailed to {implementer}, {len(done['standing'])} already mailed")
    return 0
