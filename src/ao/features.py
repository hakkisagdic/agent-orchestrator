"""Features you can switch off, each with the share of spend it costs.

The honest answer to "what does this bureaucracy cost" is a menu, not a number.
Everything ao does that spends a model's quota is a feature with a switch; with
all of them off, ao is a deterministic monitor — board, mailbox, gates, commit
authority by digest, alarms, pings, hooks — and costs nothing. With all of them
on it costs about a quarter of the implementer's spend on a normal day. The
shares below were measured on the first pilot (`ao cost`) and are refined by
it. What each costs is measured, never estimated: `ao cost --features` (#10).
"""
import json
import os
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

# key: (label, default, what it spends). What each costs is measured by `ao cost --features` (#10).
FEATURES = {
    "review":           ("independent review of every slice", True,
                         "reviewer model reads the slice diff, 1–2 times per slice"),
    "inventory_review": ("inventory-first review on slices that open a new surface", True,
                         "reviewer model, +1–2 reviews on such slices; none on fix slices"),
    "nudge":            ("watchdog restarts the idle implementer", True,
                         "implementer turns; only the empty ones are overhead"),
    "architect_wake":   ("anomalies and decisions wake the architect", True,
                         "one architect turn per anomaly batch"),
    "refill":           ("an empty queue wakes the architect to refill it", True,
                         "one architect turn per refill"),
    "reports":          ("implementer reports on state changes (start/done/blocked)", True,
                         "a few implementer tool calls per slice"),
    "hunter":           ("a scheduled, read-only bug hunt that mails leads to the architect", False,
                         "one hunter call per run over a bounded slice of the tree"),
    "toast":            ("a Windows toast for what reaches a person's desktop", False,
                         "nothing: a notification on this machine"),
}
ORDER = list(FEATURES)


def switches(cfg):
    """{key: bool} for this project: config overrides, defaults otherwise."""
    conf = cfg.get("features") or {}
    return {k: bool(conf.get(k, FEATURES[k][1])) for k in ORDER}


def enabled(cfg, key):
    return switches(cfg).get(key, FEATURES.get(key, ("", True))[1])


def set_switch(root, key, on):
    """Switch one feature in `.ao/config.json`, written whole or not at all.

    A config ao cannot read is refused rather than rebuilt: rebuilding would drop
    whatever else it held, `capability_matrix` among it (#56).
    """
    from . import lib as A
    document = A.project_config_document(root)
    if document["problem"]:
        raise ValueError(document["problem"])
    cfg = document["config"]
    cfg.setdefault("features", {})[key] = bool(on)
    A.write_project_config(root, json.dumps(cfg, indent=2, ensure_ascii=False))
    return cfg["features"]
