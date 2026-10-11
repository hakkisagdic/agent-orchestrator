#!/usr/bin/env python3
"""What ao's ledgers show about its guarantee on the checkout they belong to, measured rather than stated (EVIDENCE).

docs/evidence.md gives these numbers for ao's own repository, and a number about the guarantee should be one a reader
can make again. This prints each of them: the commits on a revision's first-parent line since the first grant, and how
many have a tree a grant bound; the commits that carry Ao- trailers, judged by the functions `ao commit-check --range`
judges them with; the reviews by kind, verdict, reviewer family and tier, with the findings they counted; and the
waivers opened and how each closed.

It only reads. ao takes a ledger's lock to append to it, and a reader that took the lock in a checkout ao is working in
would wait on it and write beside it, so the ledgers are read without it - a row is read once its line is whole - and
each chain is validated with ao's own check. `--state` names a copy of `.ao/` and the reviews folder taken at one
moment, so a checkout in use is measured as it stood. A clone keeps no ledgers (`.ao/` is a checkout's own), and there
only the trailers are checked, by tree alone, as CI checks them. Standard library only, with the ao of the checkout this
file is in.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

from ao import lib as A, storage  # noqa: E402
from ao.verdicts import REVIEWER_VERDICTS, VERDICTS  # noqa: E402

UTF8 = "utf-8"
TIERS = {"independent": "another model family", "same-family": "same family", "person": "person", None: "none"}


def _day(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime("%Y-%m-%d")


def _git(root, *args):
    return subprocess.run([A.git_binary(), *args], cwd=root, capture_output=True, text=True, encoding=UTF8,
                          errors="replace", check=True).stdout


def _ledger(state, name, chain, legacy_prefix=False):
    """Every committed row of one chained ledger, its sealed rows first, once the chain validates; None when absent.

    Read as ao reads it, less the lock: an append in flight leaves a line without its newline, which is no row.
    """
    path = os.path.join(state, ".ao", "ledger", name)
    if not os.path.exists(path):
        return None
    seal = storage._load_seal(path)
    rows = storage._read_jsonl_unlocked(path)
    storage._validate_chained_rows(path, rows, chain, storage.CHAIN_PREVIOUS_FIELD, legacy_prefix, seal=seal)
    return list(storage.sealed_rows(path)) + rows


def _reviews_folder(state):
    try:
        with open(os.path.join(state, ".ao", "config.json"), encoding=UTF8) as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        config = {}
    return str((config if isinstance(config, dict) else {}).get("reviews") or "semantic-review")


def _family(block):
    block = block if isinstance(block, dict) else {}
    stated = block.get("stated") if isinstance(block.get("stated"), dict) else {}
    return str(block.get("family") or stated.get("family") or "").strip().lower() or None


def _commits(root, rev, authority):
    """The first-parent line of `rev` since the first grant, held to the grants: by tree, and by Ao- trailers."""
    grants = [row for row in authority or [] if isinstance(row, dict) and row.get("granted") is True]
    first = next((row for row in grants if (row.get("candidate") or {}).get("head")), None)
    span = f"{first['candidate']['head']}..{rev}" if first else rev
    listed = {commit["sha"]: commit for commit in A.landed_commits(root, span)}
    line = [listed[sha] for sha in reversed(_git(root, "rev-list", "--first-parent", span).split()) if sha in listed]
    plain = [commit for commit in line if len(commit["parents"]) <= 1]
    out = {"span": span, "commits": len(line), "merges": len(line) - len(plain)}
    if first:
        trees = {(row.get("candidate") or {}).get("index_tree") for row in grants}
        out.update(first_grant=first["token"], first_grant_day=_day(first["at"]),
                   tree_bound=sum(1 for commit in plain if commit["tree"] in trees))
    tokens = {row["token"]: row for row in authority or [] if isinstance(row, dict) and isinstance(row.get("token"), str)}
    at = next((index for index, commit in enumerate(plain) if A._ao_trailers(commit["trailers"])), None)
    since = plain[at:] if at is not None else []
    out.update(first_trailer=since[0]["sha"][:12] if since else None,
               since_first_trailer=len(since),
               carrying=sum(1 for commit in plain if A._ao_trailers(commit["trailers"])),
               trailers_match=sum(1 for commit in since if not A.trailer_problems(commit, tokens if authority else None)),
               grant_on_record=sum(1 for commit in since
                                   if (A._ao_trailers(commit["trailers"]).get("Ao-Grant") or [None])[0] in tokens))
    return out


def _authority(rows):
    grants = [row for row in rows if isinstance(row, dict) and row.get("granted") is True]
    neither = [row for row in grants if not row.get("review") and not row.get("waiver")]
    return {"rows": len(rows), "grants": len(grants), "refusals": len(rows) - len(grants),
            "on_review": sum(1 for row in grants if row.get("review")),
            "on_waiver": sum(1 for row in grants if row.get("waiver") and not row.get("review")),
            "on_neither": len(neither),
            "on_neither_days": sorted({_day(row["at"]) for row in neither if isinstance(row.get("at"), (int, float))})}


def _reviews(state, rows):
    """Each review the ledger holds, read from its artefact's evidence: kind, verdict, family, tier and counts."""
    folder = os.path.join(state, _reviews_folder(state))
    kinds, families = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    tiers, counts = collections.Counter(), collections.Counter()
    held, ranges, authored, same = 0, 0, 0, []
    for row in rows:
        try:
            with open(os.path.join(folder, str(row.get("artefact"))), "rb") as fh:
                data = fh.read()
        except OSError:
            data = None
        if data is not None and "sha256:" + hashlib.sha256(data).hexdigest() == row.get("sha256"):
            held += 1
        evidence = (A.review_evidence(data.decode(UTF8, "replace")) if data else None) or {}
        reviewer = evidence.get("reviewer") if isinstance(evidence.get("reviewer"), dict) else {}
        family = "person" if evidence.get("transport") == "person" else _family(reviewer)
        verdict = row.get("verdict")
        kinds[str(row.get("kind"))][verdict] += 1
        tiers[TIERS.get(row.get("tier"), str(row.get("tier")))] += 1
        if verdict in REVIEWER_VERDICTS:
            families[family or "none"][verdict] += 1
            for key, value in (evidence.get("counts") or {}).items():
                counts[key] += int(value or 0)
        if row.get("kind") == "commit-range":
            ranges += 1
            author = _family(row.get("author"))
            authored += bool(author)
            if author and family and author == family:
                same.append(row.get("artefact"))
    days = [row["at"] for row in rows if isinstance(row.get("at"), (int, float))]
    return {"reviews": len(rows), "artefacts_held": held,
            "from": _day(min(days)) if days else None, "to": _day(max(days)) if days else None,
            "by_kind": {kind: dict(verdicts) for kind, verdicts in kinds.items()},
            "by_family": {family: dict(verdicts) for family, verdicts in families.items()},
            "tiers": dict(tiers), "counts": dict(counts), "ranges": ranges, "ranges_with_author_family": authored,
            "reviewed_by_author_family": same}


def _waivers(rows):
    opened = [row for row in rows if isinstance(row, dict) and row.get("event") == "waived"]
    closings = [row for row in rows if isinstance(row, dict) and row.get("event") == "closed"]
    closed = {row.get("id") for row in closings}
    outcomes = collections.Counter()
    for row in closings:
        said = str(row.get("outcome") or "")
        outcomes["review " + said.split(":", 1)[1].strip() if said.startswith("reviewed:")
                 else "move proof" if said.startswith("closed by proof") else said] += 1
    days = [row["at"] for row in opened if isinstance(row.get("at"), (int, float))]
    return {"opened": len(opened), "closed": len(closed), "open": sorted(row.get("id") for row in opened
                                                                        if row.get("id") not in closed),
            "gates": dict(collections.Counter(row.get("gate") for row in opened)), "outcomes": dict(outcomes),
            "from": _day(min(days)) if days else None, "to": _day(max(days)) if days else None}


def measure(root, state, rev):
    """Every number docs/evidence.md gives, for `rev` of the git checkout at `root` and the ledgers at `state`."""
    authority = _ledger(state, "authority.jsonl", A.AUTHORITY_CHAIN)
    reviews = _ledger(state, "reviews.jsonl", A.REVIEW_CHAIN)
    waivers = _ledger(state, "waivers.jsonl", A.WAIVER_CHAIN, legacy_prefix=True)
    out = {"rev": _git(root, "rev-parse", "--short=12", rev).strip(), "ledgers": authority is not None,
           "commits": _commits(root, rev, authority)}
    if authority is not None:
        out["authority"] = _authority(authority)
    if reviews is not None:
        out["reviews"] = _reviews(state, reviews)
    if waivers is not None:
        out["waivers"] = _waivers(waivers)
    return out


def _listed(counter, order=VERDICTS):
    parts = [f"{key} {counter[key]}" for key in order if counter.get(key)]
    return ", ".join(parts + [f"{key} {n}" for key, n in counter.items() if key not in order and n])


def report(found):
    """The numbers as a person reads them, one line a claim."""
    c = found["commits"]
    lines = [f"first-parent line of {found['rev']}"
             + (f" since the first grant, {c['first_grant']} of {c['first_grant_day']}" if c.get("first_grant") else "")]
    lines.append(f"  {c['commits']} commits, {c['merges']} merges")
    if c.get("first_grant"):
        lines.append(f"  {c['tree_bound']} of {c['commits'] - c['merges']} have a tree a grant bound")
    if c["first_trailer"]:
        lines.append(f"  {c['carrying']} carry Ao- trailers; from the first, {c['first_trailer']}, on: "
                     f"{c['trailers_match']} of {c['since_first_trailer']} match the grant they name"
                     + (f", {c['grant_on_record']} of them on record" if found["ledgers"]
                        else ", checked by tree alone: this checkout keeps no ledgers"))
    if not found["ledgers"]:
        return "\n".join(lines)
    a = found.get("authority") or {}
    lines.append(f"authority ledger: {a['rows']} rows: {a['grants']} granted, {a['refusals']} refused; the grants "
                 f"rest on a review {a['on_review']}, a waiver {a['on_waiver']}, neither {a['on_neither']}"
                 + (f" ({', '.join(a['on_neither_days'])})" if a["on_neither_days"] else ""))
    r = found.get("reviews")
    if r:
        lines.append(f"reviews: {r['reviews']}, {r['from']} to {r['to']}; artefacts holding the bytes the ledger "
                     f"recorded: {r['artefacts_held']}")
        for kind, said in (("index-candidate", "staged candidates"), ("commit-range", "landed ranges")):
            verdicts = r["by_kind"].get(kind)
            if verdicts:
                lines.append(f"  {said}: {sum(verdicts.values())} ({_listed(verdicts)})")
        lines.append("  by reviewer family: " + ", ".join(
            f"{family} {sum(verdicts.values())} ({_listed(verdicts)})"
            for family, verdicts in sorted(r["by_family"].items(), key=lambda item: -sum(item[1].values()))))
        lines.append("  tier: " + ", ".join(f"{tier} {r['tiers'].get(tier, 0)}" for tier in TIERS.values()))
        lines.append("  findings they counted: " + ", ".join(f"{key} {r['counts'].get(key, 0)}"
                                                         for key in ("BLOCKER", "HIGH", "MEDIUM", "LOW")))
        lines.append(f"  landed ranges recording their author's family: {r['ranges_with_author_family']} of "
                     f"{r['ranges']}; reviewed by that family: {len(r['reviewed_by_author_family'])}")
    w = found.get("waivers")
    if w:
        lines.append(f"waivers: {w['opened']} opened ({', '.join(f'{gate} {n}' for gate, n in w['gates'].items())}), "
                     f"{w['from']} to {w['to']}; {w['closed']} closed, {len(w['open'])} open")
        lines.append("  closed on: " + ", ".join(f"{how} {n}" for how, n in sorted(w["outcomes"].items(),
                                                                                key=lambda item: -item[1])))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="What ao's ledgers show about its guarantee, measured (EVIDENCE).")
    parser.add_argument("--root", default=REPO, help="the git checkout (default: the one this file is in)")
    parser.add_argument("--state", help="where .ao/ and the reviews folder are (default: --root); a copy taken at "
                                        "one moment measures a checkout in use as it stood")
    parser.add_argument("--rev", default="main", help="the revision whose first-parent line is measured (default main)")
    parser.add_argument("--json", action="store_true", help="every number as one JSON document")
    args = parser.parse_args(argv)
    try:
        found = measure(args.root, args.state or args.root, args.rev)
    except storage.LedgerCorruption as exc:
        print(f"a ledger does not validate, so nothing is measured: {exc}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"the checkout cannot be read: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(found, indent=2, sort_keys=True) if args.json else report(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
