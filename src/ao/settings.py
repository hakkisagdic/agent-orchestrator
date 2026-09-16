"""What a person can set that the code used to decide for them (#74).

Every threshold here has been wrong for some project at least once, and each time
the fix was to edit ao's own source. They are settings now, each with a documented
default, resolved in this module and nowhere else: the project's `.ao/config.json`
first, then the machine's `~/.ao/settings.json`, then the default below. `ao config`
reads and writes them and docs/configuration.md lists them; a test keeps the
registry, the documentation and the code that reads them in agreement.

A value that is present but unusable - a string where a number belongs, a number
out of range - is not used, and `ao doctor` says what was set and what ao uses.
"""
import json
import os
from collections import namedtuple

UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

Setting = namedtuple("Setting", "default kind minimum maximum scope text")

# scope: "project" settings are read from the project first; "machine" settings
# govern state shared by every project on the machine and are read from there only.
SETTINGS = {
    "round_budget": Setting(
        5, int, 1, None, "project",
        "review rounds a slice may spend before the watchdog stops nudging and tells a person"),
    "review.max_inflight": Setting(
        2, int, 1, None, "project",
        "submitted reviews that may run at once; a submit beyond it names what to collect"),
    "review.unhandled_minutes": Setting(
        30, int, 1, None, "project",
        "minutes a returned review may wait uncollected before the watchdog raises it"),
    "review_timeout": Setting(
        900, int, 1, None, "project",
        "seconds one reviewer may take; never taken from the command line"),
    "stall_minutes": Setting(
        60, int, 1, None, "project",
        "minutes a staged candidate may wait to land before throughput calls the slice stalled"),
    "gates.default_timeout": Setting(
        600, int, 1, None, "project",
        "seconds a gate may run when its own definition names no timeout"),
    "gates.coverage_min_files": Setting(
        5, int, 1, None, "project",
        "source files of one toolchain a top-level tree must hold before a gate must exercise it"),
    "watchdog.idle_minutes": Setting(
        6.0, float, 1, None, "project",
        "minutes of implementer silence before the watchdog acts"),
    "watchdog.max_attempts": Setting(
        3, int, 1, None, "project",
        "nudges without progress before a person is told the implementer is stuck"),
    "decisions.human_after_minutes": Setting(
        15, int, 1, None, "project",
        "minutes an open decision waits before it rings a person"),
    "waivers.default_hours": Setting(
        24, int, 1, None, "project",
        "hours a review waiver stays open when --hours is not given"),
    "waivers.max_hours": Setting(
        168, int, 1, None, "project",
        "the longest a review waiver may be granted for"),
    "alarms.red_after_minutes": Setting(
        60, int, 1, None, "project",
        "minutes an orange alarm stands before it turns red and sends one e-mail"),
    "fanout.max_agents": Setting(
        12, int, 1, None, "project",
        "the most agents one fan-out may start"),
    "fanout.per_agent_tokens": Setting(
        50_000, int, 1, None, "project",
        "the token budget each fanned-out agent is given"),
    "fanout.window_reserve_pct": Setting(
        30, int, 0, 100, "project",
        "percent of the provider window a fan-out must leave unused"),
    "implementer.name": Setting(
        "kiro", str, None, None, "project",
        "the implementer's name in mail file names"),
    "architect.name": Setting(
        "fable", str, None, None, "project",
        "the architect's name in mail file names"),
    "alarms.red_repeat_hours": Setting(
        6, int, 1, None, "machine",
        "hours before a red alarm that still stands e-mails again"),
    "alarms.reset_after_hours": Setting(
        2, int, 1, None, "machine",
        "hours of quiet after which an alarm episode is over"),
    "quota.block_percent": Setting(
        97, int, 1, 100, "machine",
        "a provider window used at or above this percent stops a wake or a nudge"),
    "architect.quota_window_hours": Setting(
        5, int, 1, None, "machine",
        "the architect's usage window; a reset named further away is not the one meant"),
    "heartbeat.retired_days": Setting(
        7, int, 1, None, "machine",
        "days of watchdog silence after which a project counts as retired, not dead"),
    "fleet.window_reserve_pct": Setting(
        20, int, 0, 100, "machine",
        "percent of the machine's provider window kept free before a report wake"),
    "binaries.extra_dirs": Setting(
        [], list, None, None, "machine",
        "directories searched for agent binaries after PATH, before the usual install locations"),
}

_MISSING = object()


def default(key):
    value = SETTINGS[key].default
    return list(value) if isinstance(value, list) else value


def machine_path():
    from . import lib as A
    return os.environ.get("AO_SETTINGS") or os.path.join(A.HOME, ".ao", "settings.json")


def machine_settings():
    try:
        with open(machine_path(), encoding=UTF8) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def lookup(document, key):
    node = document
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def usable(key, value):
    """Whether a value can be used for this setting."""
    spec = SETTINGS[key]
    if spec.kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    elif spec.kind is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
    elif spec.kind is str:
        return isinstance(value, str) and bool(value.strip()) and "/" not in value and "\\" not in value
    elif spec.kind is list:
        return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)
    if spec.minimum is not None and value < spec.minimum:
        return False
    if spec.maximum is not None and value > spec.maximum:
        return False
    return True


def expected(key):
    spec = SETTINGS[key]
    if spec.kind is list:
        return "a list of directory names"
    if spec.kind is str:
        return "a name with no path separator"
    words = {int: "a whole number", float: "a number"}[spec.kind]
    if spec.minimum is not None and spec.maximum is not None:
        return f"{words} from {spec.minimum} to {spec.maximum}"
    if spec.minimum is not None:
        return f"{words} of at least {spec.minimum}"
    return words


def resolve(cfg, key):
    """(value, source, problem) for one setting.

    A project setting is read from the project, then the machine; a machine
    setting from the machine only. The first usable value wins; one that is set
    but unusable is passed over and named in `problem`.
    """
    spec = SETTINGS[key]
    layers = [("machine", machine_settings())]
    if spec.scope == "project":
        layers.insert(0, ("project", cfg or {}))
    problem = None
    for source, document in layers:
        value = lookup(document, key)
        if value is _MISSING:
            continue
        if usable(key, value):
            return (list(value) if isinstance(value, list) else value), source, problem
        problem = problem or (f"{key} is set to {value!r} in the {source} settings, which is not "
                              f"{expected(key)}")
    return default(key), "default", problem


def get(cfg, key):
    return resolve(cfg, key)[0]


def parse(key, text):
    """A value from the command line, as the setting's kind, or ValueError."""
    spec = SETTINGS[key]
    if spec.kind is int:
        value = int(text)
    elif spec.kind is float:
        value = float(text)
    elif spec.kind is list:
        value = [part.strip() for part in str(text).split(",") if part.strip()]
    else:
        value = str(text).strip()
    if not usable(key, value):
        raise ValueError(f"{key} must be {expected(key)}")
    return value


def assign(document, key, value):
    """Set or, with value _MISSING, remove one dotted key in a config document."""
    parts = key.split(".")
    node = document
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            if value is _MISSING:
                return document
            child = node[part] = {}
        node = child
    if value is _MISSING:
        node.pop(parts[-1], None)
    else:
        node[parts[-1]] = value
    return document


def write_machine(document):
    from .storage import replace_file_durably
    replace_file_durably(machine_path(), (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(UTF8))


def problems(cfg):
    """(key, text) for every setting whose effective value is not what was written.

    Covers values that are set but unusable, project settings placed where only
    the machine is read, and names under a settings group that ao does not read.
    """
    out = []
    machine = machine_settings()
    for key, spec in SETTINGS.items():
        _, _, problem = resolve(cfg, key)
        if problem:
            out.append((key, f"{problem}; ao uses {resolve(cfg, key)[0]!r}"))
        if spec.scope == "machine" and lookup(cfg or {}, key) is not _MISSING:
            out.append((key, f"{key} is set in the project, but it governs the whole machine and is read "
                             f"only from {machine_path()}: `ao config set {key} … --machine`"))
    groups = {key.split(".")[0] for key in SETTINGS if "." in key} - {"implementer", "architect"}
    for source, document in (("project", cfg or {}), ("machine", machine)):
        for group in sorted(groups):
            section = document.get(group)
            if not isinstance(section, dict):
                continue
            for name in sorted(section):
                if f"{group}.{name}" not in SETTINGS:
                    out.append((f"{group}.{name}", f"{group}.{name} in the {source} settings is not a setting "
                                                   "ao reads; `ao config list` shows the names"))
    for name in sorted(machine):
        if name not in SETTINGS and name not in groups:
            out.append((name, f"{name} in the machine settings is not a setting ao reads"))
    return out
