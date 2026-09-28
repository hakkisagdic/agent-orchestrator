# Security policy

ao decides what may be committed in a repository where AI agents also work, and it runs as you, on
your machine, beside them. A flaw in it is a flaw in that decision, so report one privately first.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: on the repository's **Security** tab, choose
**Report a vulnerability**, or open
[a new advisory](https://github.com/hakkisagdic/agent-orchestrator/security/advisories/new) directly.
The report and everything said about it stay between you and the maintainer until an advisory is
published. Please do not open a public issue, pull request or discussion about it, and do not attach
a real credential: a made-up one shows a leak just as well.

A report is most useful when it names:

- the version (`ao --version`) or the commit, and the platform;
- the smallest sequence that reproduces it, from a fresh repository where it can;
- what it lets an agent, a reviewer or another user do that ao says cannot be done, and where ao
  says so: a document, a refusal, a test.

A failing test is the best evidence there is; [tests/test_attacks.py](tests/test_attacks.py) shows
the shape of one.

What happens next: the report is answered in its advisory. A fix lands on `main` with a test that
reproduces what was reported and ships in the next release, and the advisory is published with that
release, crediting the reporter unless they ask not to be named.

## Supported versions

| Version | Security fixes |
|---|---|
| `main` | yes: every fix lands here first |
| the latest release on PyPI | yes, in the next release |
| any earlier release | no: upgrade to the latest |

## In scope

A report is in scope when it breaks something ao's documents say holds. Three kinds matter most:

- **Commit authority bypassed.** A commit lands in a governed repository without what `ao commit-ok`
  and the pre-commit hook require - a passed verification of exactly the staged candidate, an approved
  review by an actor other than the one that wrote it or a waiver on the record, an unedited plan - or
  ao grants on evidence forged, replayed or rewritten in a way its documents say it detects: a ledger
  row, a review artefact, a gate definition. ao never grants an agent `push` - the window
  `ao push allow` opens is a person's - and anything that makes it grant one is in scope too
  ([safety.md](docs/safety.md), [ledger.md](docs/ledger.md)).
- **A secret exposed.** ao copies a credential - a token, a key, a password, a harness's login - into
  something it writes into a repository (a ledger, a review, the mailbox, the board), into a message or
  an alarm it sends (Telegram, e-mail), into a prompt it hands a reviewer, or into a log; or it writes
  a credential file of its own, `~/.ao/email.json` or `~/.ao/pings.json`, readable by another user
  ([privacy.md](docs/privacy.md)).
- **A reviewer escaping its sandbox.** ao starts a reviewer with no tool but reading
  (`options.trust_none`, [adapters.md](docs/adapters.md)), in a directory outside the repository that
  holds a copy of the candidate, and hands it the candidate as data. A reviewer that writes the
  repository or anything outside that directory, runs a command, reaches the implementer's session or
  has its answer recorded as another actor's is in scope; so is a candidate that makes ao read a
  verdict the reviewer did not give.

So is the way ao reaches other machines: a release that publishes what its tag does not hold
([release.yml](.github/workflows/release.yml)), or a package that ships a file the repository does not.

## Out of scope

What the [safety model](docs/safety.md) leaves out, or leaves to a person on the record:

- a process running as your own user: it can rewrite what ao keeps, and ao detects some of that
  rewriting without claiming to exclude it ([safety.md](docs/safety.md), the ledger's chain);
- what an agent CLI or a model provider does with what it is sent, and flaws in those tools
  themselves: report those to their vendors;
- an adapter file you installed: it is executable configuration, and it names the commands ao runs.
  An agent that can change which adapter ao reads is in scope;
- a harness's own sandbox that a person turned off on the record, with `watchdog.bypass_adapters`;
- trust between machines, which is keyflip's ([keyflip.md](docs/keyflip.md),
  [safety.md §9](docs/safety.md#9-what-this-model-does-not-cover)).

## This repository

*In ao since slice TRUST-HYGIENE: no credential and no home directory of the machine it was written on
is committed here, and a test keeps it so. `tests/test_tracked_hygiene.py` reads every tracked file
for every credential shape ao keeps out of what it writes, a passphrase assigned to a password among
them, for a long generated value assigned to any name that says it is secret, such as
`ENCRYPTION_KEY`, and for `/Users/<name>`, `/home/<name>` and `C:\Users\<name>`. An assigned value
that is called is code, `password = os.environ.get(...)`, and is passed over. The few deliberate
fixtures stand in `tests/hygiene-allowlist.txt`: a credential there named by the start of its SHA-256
and never by its value, a home by its account in the one file it stands in. An allowance that covers
nothing fails until it is dropped. The test suite runs on a machine of its own, a temporary home and
PATH and none of your `AO_` variables: running it reads none of your `~/.ao` or the files those
variables name and messages none of your channels, and no agent CLI of yours is on its PATH or under
its home.*
