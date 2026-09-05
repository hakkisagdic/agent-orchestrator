"""Features you can switch off, each with the share of spend it costs.

The honest answer to "what does this bureaucracy cost" is a menu, not a number.
Everything ao does that spends a model's quota is a feature with a switch; with
all of them off, ao is a deterministic monitor — board, mailbox, gates, commit
authority by digest, alarms, pings, hooks — and costs nothing. With all of them
on it costs about a quarter of the implementer's spend on a normal day. The
shares below were measured on the first pilot (`ao cost`) and are refined by
it; `ao features` prints the estimate for the current switches.
"""
import json
import os

# key: (label, default, share of implementer spend in %, what it spends)
FEATURES = {
    "review":           ("independent review of every slice", True, 8,
                         "reviewer model reads the slice diff, 1–2 times per slice"),
    "inventory_review": ("inventory-first review on slices that open a new surface", True, 4,
                         "reviewer model, +1–2 reviews on such slices; none on fix slices"),
    "nudge":            ("watchdog restarts the idle implementer", True, 4,
                         "implementer turns; only the empty ones are overhead"),
    "architect_wake":   ("anomalies and decisions wake the architect", True, 3,
                         "one architect turn per anomaly batch"),
    "refill":           ("an empty queue wakes the architect to refill it", True, 2,
                         "one architect turn per refill"),
    "reports":          ("implementer reports on state changes (start/done/blocked)", True, 2,
                         "a few implementer tool calls per slice"),
}
ORDER = list(FEATURES)


def switches(cfg):
    """{key: bool} for this project: config overrides, defaults otherwise."""
    conf = cfg.get("features") or {}
    return {k: bool(conf.get(k, FEATURES[k][1])) for k in ORDER}


def enabled(cfg, key):
    return switches(cfg).get(key, FEATURES.get(key, ("", True))[1])


def estimate(cfg):
    """Estimated share of implementer spend for the current switches, in %."""
    on = switches(cfg)
    return sum(FEATURES[k][2] for k in ORDER if on[k])


def set_switch(root, key, on):
    p = os.path.join(root, ".ao", "config.json")
    cfg = json.load(open(p))
    cfg.setdefault("features", {})[key] = bool(on)
    json.dump(cfg, open(p, "w"), indent=2, ensure_ascii=False)
    return cfg["features"]
