# Cross-family review in CI

`action.yml` at the root of this repository is a GitHub Action. In a pull request's workflow it has
the change reviewed by a model of another family than the one that wrote it, in ao's verdict format,
and writes the verdict and the findings into the job's summary before anyone merges. It runs
`ao ci review` with ao's own API reviewer, so the runner needs no coding agent: an OpenAI-compatible
endpoint and a key are enough, and the repository needs no ao of its own.

## What it guarantees, and what it does not

- **It reviews; it grants nothing.** No commit authority comes from it, and it gates no `git commit`:
  in a repository ao governs that is `ao commit-ok` and the commit hook ([safety](safety.md)). With
  `fail-on`, a NEEDS_CHANGES or a review that gave no verdict fails the job, and a branch protection
  rule that requires the job is what stops a merge; setting one is yours.
- **The author's family is the workflow's statement, not a measurement.** ao cannot see which model
  wrote a pull request. A reviewer of the family the workflow states is refused before it runs; with
  `unknown`, the review runs and claims no independence, and its tier reads `not established`.
- **What the change says is read as a claim.** The pull request's title and its commit messages are
  handed to the reviewer as claims to verify against the diff, not facts, and the reviewer reads
  nothing else of the pull request. A change can still argue with its reviewer; a verdict is a
  model's reading, not a proof, and one review is one answer.
- **ao runs nothing the change holds.** It reads the commits with git and builds, tests and runs none
  of them, and the API reviewer hands the model the change as text. The key goes to the base URL
  alone, over https, read from the variable when the request is sent
  ([adapters](adapters.md#a-reviewer-can-be-a-tool-ao-runs)).

## A workflow

The key comes from a secret into the step's environment; the action is told the variable's name,
never the key. Check out the whole history, so the base and the merge base are there:

```yaml
name: review
on:
  pull_request:
permissions:
  contents: read
jobs:
  review:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: hakkisagdic/agent-orchestrator@<commit>
        env:
          REVIEW_API_KEY: ${{ secrets.REVIEW_API_KEY }}
        with:
          model: <model>
          base-url: https://api.example.com/v1
          api-key-env: REVIEW_API_KEY
          reviewer-family: <the reviewer model's family>
          author-family: <the family of the model that writes your pull requests>
```

Pin `uses:` to a full commit, as for any action that holds a key. A pull request from a fork gets no
secrets on `pull_request`, so its review finds no key and gives no verdict: skip forks with
`if: github.event.pull_request.head.repo.full_name == github.repository`, or set `fail-on: never`.
`pull_request_target` hands secrets to a run over a fork's change; ao runs none of its code, but its
text reaches the reviewer, so use it only where you would show that text to the model anyway.

## Inputs and outputs

| input | |
|---|---|
| `model` | the reviewer model, as the API names it |
| `base-url` | the API's base URL, over https; ao asks its `/chat/completions` once |
| `api-key-env` | the name of the variable that holds the key, set from a secret in the step's `env` |
| `reviewer-family` | the reviewer model's family, as you know it: ao never infers one from a model's name |
| `author-family` | the family of the model that wrote the change, or `unknown` |
| `base`, `head` | the change: from the merge base of `base` and `head` to `head`; the pull request's own by default |
| `boundary` | what the change says it does, the pull request's title by default; read as a claim |
| `fail-on` | `needs-changes` (default): NEEDS_CHANGES or no verdict fails the job; `no-verdict`: only no verdict does; `never` |
| `ao-version` | the ao-orchestrator release installed from PyPI; one that has `ao ci review` |
| `python-version` | the Python that runs ao, 3.12 by default |

| output | |
|---|---|
| `verdict` | APPROVED, NEEDS_CHANGES, INVALID, UNAVAILABLE, or `none` when no review was recorded |
| `artefact` | the review artefact's path in the checkout, its header and the reviewer's whole answer |
| `tier` | the independence the review stands in: another model family, or not established |
| `blocker`, `high` | the BLOCKER and HIGH findings it counted |

INVALID is an answer ao could not read as a verdict and UNAVAILABLE a reviewer that could not be
reached; neither is a verdict ([protocol](protocol.md#verdicts)).

## Cost and time

One review is one completion. It carries the review prompt, the diff - ao refuses a diff over
400 KB rather than review it cut short - the code around the change at the head, up to 100 KB
(`review.context_bytes`), and the commit messages, up to 16 KB; at about four bytes a token, the
largest pull request is some 130,000 tokens in, and a small one a few thousand. A reasoning model
can spend its whole answer thinking and write no verdict ([adapters](adapters.md)); that is no
verdict, and `fail-on` decides what it does to the job.

ao's client waits ten minutes for the answer, and `review_timeout` (900 seconds) bounds the call;
give the job's `timeout-minutes` room for both and for the install. A repository that keeps an
`.ao/config.json` has its own `review_timeout` and `review.context_bytes` read from there.

## `ao ci review`

The action is three commands, and any CI can run them:

```bash
pip install ao-orchestrator
ao config set review.api_providers "ci https://api.example.com/v1 REVIEW_API_KEY" --machine
ao ci review --base origin/main --reviewer openai-api --model ci/<model> --family <family> --author-family <family>
```

`--machine` writes the provider into the machine's own ao settings, over any `review.api_providers` set there
before: on a CI runner that is a fresh machine; on your own, run the three commands somewhere else or set the
provider back afterwards.

`ao ci review` reviews the commits `--head` (HEAD by default) adds to the merge base of `--base` and
`--head` - what a pull request shows - the way `ao catchup` reviews a landed range: the diff, the code
around it at the head, the repository's threat model, and the commit messages as claims. The reviewer
is composed for this run from `--reviewer`, `--model` and `--family`, as
`ao role set reviewer <adapter> --model <model> --family <family>` composes one, and written nowhere.
`--summary <file>` appends the verdict and the findings as Markdown, and `--outputs <file>` appends
`name=value` lines: `verdict`, `artefact`, `tier`, `base`, `head` and the four counts. Nothing is
staged or committed; the review's record goes into the checkout's `.ao/ledger/reviews.jsonl` and
the reviews folder, as every review's does. It exits 0 on APPROVED and when there is nothing to
review - no commit, or commits whose diff is empty - 1 on NEEDS_CHANGES, 3 when the review gave no
verdict, and 2 when it could not review: a base the checkout lacks, a reviewer of the author's family,
a reviewer ao cannot compose, a diff over 400 KB.

*In ao since slice GITHUB-ACTION: a pull request in any repository can be reviewed by a model of
another family before it is merged. ao reviewed only where it governs, with a reviewer a person set in
the project's config, so a repository without ao had no way to have a change read by another family
first. The action and `ao ci review` reuse catch-up's review of a range and the API reviewer; nothing
is published - listing the action anywhere is the owner's step.*
