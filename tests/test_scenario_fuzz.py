"""The guard chain over random worlds, judged by the watchdog's invariants (#11).

The scenario tests prove each fault in docs/watchdog.md (F1-F19) one world at a
time, and the contradictions between guards were found one at a time as well. Here
a seed builds a world - a turn in the tree, a person's session, an orphan, the
transcript's age, the board, a blocked report, mail for the implementer, an open
decision, a hold, quota, the nudge switch, the round budget, an architect at the
keyboard - one dry cycle runs in it, and its decision is checked against every
invariant those faults established. A failure prints its seed and its world:
AO_FUZZ_SEED=<n> runs that world alone, AO_FUZZ_SEEDS=<count> runs more of them.
"""
import os
import random

import pytest

from ao import features as F, lib as A
from tests.scenarios import World

IDLE = 6 * 60
KIRO = ["/agents/kiro-cli", "chat", "--resume-id", "s1", "--no-interactive", "devam"]
PERSON = ["/agents/kiro-cli", "chat"]
ENGINE = ["/agents/Application Support/kiro-cli/node", "--experimental-wasm-modules", "x"]
SEEDS = [int(os.environ["AO_FUZZ_SEED"])] if os.environ.get("AO_FUZZ_SEED") \
    else range(int(os.environ.get("AO_FUZZ_SEEDS", "40")))


def _build(world, rng, monkeypatch):
    facts = {"age": rng.choice([30, IDLE - 30, IDLE + 60, 3 * IDLE + 60, 10 * IDLE])}
    world.transcript_age(facts["age"])
    for name, chance in (("running", 0.6), ("headless", 0.3), ("person", 0.2), ("orphan", 0.2),
                         ("hold", 0.15), ("blocked_report", 0.25), ("inbox", 0.3), ("decision", 0.15),
                         ("over_budget", 0.15), ("architect_present", 0.15)):
        facts[name] = rng.random() < chance
    facts["quota"] = rng.random() < 0.8
    facts["nudge_on"] = rng.random() < 0.85

    if facts["running"]:
        world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    if facts["headless"]:
        world.process(600, KIRO)
    if facts["person"]:
        world.process(610, PERSON, headless=False, tty="ttys001")
    if facts["orphan"]:
        world.process(300, ENGINE, ppid=1, pgid=299)
    if facts["hold"]:
        monkeypatch.setattr(A, "hold_state", lambda root: {"by": "a person", "minutes": 3, "reason": "editing"})
    if facts["blocked_report"]:
        world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", "# queue empty\n\n## KARAR GEREKLİ\n")
    if facts["inbox"]:
        world.mail("20260916-1100-fable-to-kiro-INFO-next.md", "# next\n")
    if facts["decision"]:
        A.ask(world.root, "which store keeps the ledger?", ["files", "sqlite"])
    if facts["over_budget"]:
        monkeypatch.setattr(A, "rounds", lambda root, reviews: 9)
    if facts["architect_present"]:
        world.architect()
    world.quota = facts["quota"]
    if not facts["nudge_on"]:
        switches = F.enabled
        monkeypatch.setattr(F, "enabled", lambda cfg, key: False if key == "nudge" else switches(cfg, key))
    return facts


def _nudged(trace):
    return any("· nudging" in line for line in trace)


INVARIANTS = (
    ("F9: nothing is started while a person holds the tree",
     lambda facts, trace: not (facts["hold"] and _nudged(trace))),
    ("F1/F2: a live turn in the tree is not joined by a second",
     lambda facts, trace: not (facts["headless"] and facts["age"] < 3 * IDLE and _nudged(trace))),
    ("F9: a person's session in the tree is never nudged over",
     lambda facts, trace: not (facts["person"] and _nudged(trace))),
    ("no turn is started without quota",
     lambda facts, trace: not (not facts["quota"] and _nudged(trace))),
    ("the nudge switch off means no nudge",
     lambda facts, trace: not (not facts["nudge_on"] and _nudged(trace))),
    ("F11: an implementer waiting on the architect is not nudged",
     lambda facts, trace: not (facts["blocked_report"] and _nudged(trace))),
    ("#20: an open decision is not nudged past",
     lambda facts, trace: not (facts["decision"] and _nudged(trace))),
    ("F14: over the round budget a person is told instead",
     lambda facts, trace: not (facts["over_budget"] and facts["running"] and _nudged(trace))),
    ("an implementer that is not idle is left alone",
     lambda facts, trace: not (facts["age"] < IDLE and _nudged(trace))),
    ("F19: an architect at the keyboard is not woken beside",
     lambda facts, trace: not (facts["architect_present"] and any("would wake the architect" in line
                                                                  for line in trace))),
    ("F10: an orphan is never counted as a writer",
     lambda facts, trace: not (facts["orphan"] and not facts["headless"] and not facts["person"]
                               and not facts["architect_present"]
                               and any("already in this tree" in line for line in trace))),
)


@pytest.mark.parametrize("seed", SEEDS)
def test_no_random_world_contradicts_an_invariant(seed, project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    facts = _build(world, random.Random(seed), monkeypatch)

    trace = world.cycle(dry_run=True)

    broken = [name for name, holds in INVARIANTS if not holds(facts, trace)]
    started = [argv for argv in world.spawned if isinstance(argv, list) and argv and str(argv[0]).startswith("/agents/")]
    if started:
        broken.append(f"a dry cycle started {started}")
    assert not broken, (f"seed {seed} (AO_FUZZ_SEED={seed}): {broken}\nworld: {facts}\ntrace:\n  "
                        + "\n  ".join(trace))
