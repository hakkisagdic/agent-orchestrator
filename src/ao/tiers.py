"""The review tiers: which one a reviewer stands in, decided in one place (REVIEW-TIERS).

ao lands code only after a review by a model family other than the one that wrote it. Someone
who runs a single harness could not meet that rule, and ao refused them three different ways:
`ao init` refused the single-harness profile its own documents recommended, `ao role set`
assigned a reviewer that `ao review` then refused, and the one configuration that worked - a
reviewer with no implementer - compared the reviewer with nobody. There are three tiers now,
each recorded with the review and labeled wherever the review shows:

- independent: a model of another family. The default, and the strongest.
- same-family: another model of the implementer's family. Only where a person opted in with
  `review.same_family` `labeled`, on the record, and every surface says SAME_FAMILY_LABEL.
- person: a person read the diff and recorded the verdict with `ao person-review`.

`ao role set`, the reviewer probe, `ao review`, `ao commit-ok`, the capability matrix and catch-up
all ask `tier`. What each knows about a reviewer and an author is gathered where it is known; which
tier that makes is decided here and nowhere else. Standard library only, and pure: nothing here
reads a file, a setting or a ledger.
"""

INDEPENDENT = "independent"
SAME_FAMILY = "same-family"
PERSON = "person"
TIERS = (INDEPENDENT, SAME_FAMILY, PERSON)

LABELS = {INDEPENDENT: "another model family",
          SAME_FAMILY: "same family: weaker independence",
          PERSON: "person review"}
SAME_FAMILY_LABEL = LABELS[SAME_FAMILY]

# `review.same_family`: its default, and the value a person opts in with.
REFUSED = "refused"
LABELED = "labeled"

# A route may not declare this family: only a review a person recorded is a person's.
PERSON_FAMILY = "person"

# What a refusal names as the ways forward, wherever a model reviewer of no tier is refused.
WAYS_FORWARD = ("Ways forward: a reviewer of another model family (ao role set reviewer <adapter> --model <model>); "
                "another model of the implementer's family, labeled, once a person opts in on the record "
                "(ao config set review.same_family labeled --by <name>); or a person's review of each candidate "
                "(ao person-review --by <name>).")
# A waived range records the family that wrote it and never its model, so no same-family tier reaches it.
RANGE_WAYS_FORWARD = ("Ways forward: a reviewer of another model family, or a person's review of the range "
                      "(ao person-review --commits <range> --by <name>).")


def label(tier):
    """The words every surface prints for a tier; '' for none."""
    return LABELS.get(tier, "")


def weaker(tier):
    """Whether a tier is one a surface must call out: anything below another model family."""
    return tier in (SAME_FAMILY, PERSON)


def tier(reviewer, author, same_family=False):
    """(tier, None) for the tier a reviewer stands in against the author of what it reviews, or (None, refusal).

    `reviewer` is what is known of the reviewer, each key optional:
      person   - a person read the diff and recorded the verdict; nothing a route declares sets it
      identity - it runs as the author: the implementer's session, or its capability binding
      tool     - it is a tool that reaches many model families through its provider
      family   - the family it states itself, lowercased
      declared - the family it or its adapter declares, None when none does or the two disagree
      model    - the model it runs
      engine   - the program it runs
    `author` is what is known of whoever wrote the work:
      range    - a waived range under catch-up, held to `families`: recorded or named for it
      known    - an implementer is configured at all
      family, declared, model - as for the reviewer
      engines  - the programs the implementer's adapter runs
    `same_family` is whether a person's opt-in into the same-family tier is in force.

    A refusal is a base - `author`, `person-family`, `tool-unnamed`, `range-unnamed`,
    `unnamed-family`, `author-family`, `family` or `engine` - and, where the opt-in is in force and
    still admits nothing, `/families`, `/model-unnamed` or `/model` after it. (None, None) is an
    implementer nobody configured: no tier can be established and nothing is refused, which
    `ao doctor` names as a problem rather than a review surface guessing a tier.
    """
    reviewer = reviewer if isinstance(reviewer, dict) else {}
    author = author if isinstance(author, dict) else {}
    if reviewer.get("person"):
        return PERSON, None
    if reviewer.get("identity"):
        return None, "author"
    if PERSON_FAMILY in (reviewer.get("family"), reviewer.get("declared")):
        return None, "person-family"
    if reviewer.get("tool") and not reviewer.get("family"):
        # A model name is not a family, and a tool's provider reaches many (#86).
        return None, "tool-unnamed"
    if author.get("range"):
        # A waived range is held to the family that wrote it; nothing recorded its model (#65).
        families = author.get("families") or ()
        if not families:
            return None, "range-unnamed"
        if not reviewer.get("declared"):
            return None, "unnamed-family"
        return (INDEPENDENT, None) if reviewer["declared"] not in families else (None, "author-family")
    if not author.get("known"):
        return None, None
    stated = (reviewer.get("family"), author.get("family"))
    if all(stated):
        if stated[0] != stated[1]:
            return INDEPENDENT, None
        base = "family"
    elif reviewer.get("engine") and reviewer["engine"] in (author.get("engines") or ()):
        # One engine serves many models; with families unstated, sharing it is sharing the author (#65).
        base = "engine"
    else:
        return INDEPENDENT, None
    if not same_family:
        return None, base
    declared = (reviewer.get("declared"), author.get("declared"))
    if not all(declared) or declared[0] != declared[1]:
        return None, base + "/families"
    models = (reviewer.get("model"), author.get("model"))
    if not all(models):
        return None, base + "/model-unnamed"
    if str(models[0]).strip().lower() == str(models[1]).strip().lower():
        return None, base + "/model"
    return SAME_FAMILY, None
