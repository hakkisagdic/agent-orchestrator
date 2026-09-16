"""Protocols an adapter names that its data alone cannot describe (#76).

An adapter declares where a credential lives, which command names the account and
which endpoint answers. The request and the shape of the answer are a protocol,
and a protocol is code. Each driver here is chosen by the name an adapter gives it
in `billing.api.driver` and reads every path, key, command and endpoint from that
adapter, so no driver names a harness.
"""
import json
import os
import subprocess
import time

UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252


def usage_limits(api, timeout=20):
    """Real credit usage from the provider, not an estimate.

    A usage-limits endpoint returns exactly what the app's dashboard shows: credits
    used, the plan limit, the reset date, overage settings. It authenticates with
    the OIDC access token the CLI already holds after login, read from its local
    store (`api.token`) — the same credential, on the same machine, for the same
    account; the profile it asks about is the line the CLI's `api.profile` command
    prints with the declared prefix.

    Two things this is not. It is not an API key: such a key is rejected as a
    bearer token here, so it authenticates something else. And it is not
    guesswork from transcripts — that reading exists as an offline fallback and
    undercounts by whatever ran on another machine, which measured about a third.

    Returns None when there is no usable token; the caller falls back rather than
    presenting an error as a balance. The token is used and never stored, logged
    or returned. When the CLI cannot be found or run it returns {"error": ...}
    naming the step, so a caller can report a broken check instead of a silent one.
    """
    from . import lib as A
    token = api.get("token") or {}
    db = A._home_path(token.get("sqlite"))
    if not db or not os.path.exists(db):
        return None
    raw = A.sh(f"sqlite3 {json.dumps(db)} "
               f"\"SELECT value FROM {token['table']} WHERE key='{token['key']}';\"")
    if not raw:
        return None
    try:
        tok = json.loads(raw)
    except Exception:
        return None
    access = tok.get(token.get("field") or "access_token")
    if not access:
        return None
    expires = token.get("expires") or "expires_at"
    if tok.get(expires):
        try:
            exp = tok[expires]
            exp = float(exp) if not isinstance(exp, str) else \
                __import__("datetime").datetime.fromisoformat(
                    exp.replace("Z", "+00:00")).timestamp()
            if exp < time.time():
                return {"expired": True}
        except Exception:
            pass

    # Resolve the CLI through the harness binary search, not the ambient PATH: under
    # launchd that PATH is /usr/bin:/bin:/usr/sbin:/sbin, the CLI was never found,
    # and a `2>/dev/null` turned the miss into None, so the watchdog never recorded
    # a sample and the exhaustion alarm could not fire. Say which step failed.
    profile = api.get("profile") or {}
    argv = [str(part) for part in profile.get("argv") or []]
    found = A.binary_candidates(argv[0]) if argv else []
    if not found:
        return {"error": f"{argv[0] if argv else 'the CLI'} is not on PATH or in the usual install directories"}
    step = " ".join(argv[1:])
    try:
        prof = subprocess.run([found[0]] + argv[1:], capture_output=True, text=True, encoding=UTF8,
                              errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": f"{found[0]} {step} could not run ({type(exc).__name__})"}
    arn = next((line.strip() for line in (prof.stdout or "").splitlines()
                if line.strip().startswith(profile.get("prefix") or "arn:")), "")
    if not arn:
        return {"error": f"{found[0]} {step} returned no profile ARN (exit {prof.returncode})"}

    import urllib.error
    import urllib.request
    body = {key: (arn if value == "{profile}" else value) for key, value in (api.get("body") or {}).items()}
    req = urllib.request.Request(
        api["endpoint"],
        data=json.dumps(body).encode(),
        headers={"Content-Type": api["content_type"],
                 "x-amz-target": api["target"],
                 "Authorization": "Bearer " + access})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except Exception:
        return None

    resource = api.get("resource") or "CREDIT"
    row = next((b for b in d.get("usageBreakdownList") or []
                if b.get("resourceType") == resource), None)
    if not row:
        return {"error": f"no {resource} row in response"}
    sub = d.get("subscriptionInfo") or {}
    over = d.get("overageConfiguration") or {}
    return {
        "used": row.get("currentUsageWithPrecision", row.get("currentUsage")),
        "limit": row.get("usageLimitWithPrecision", row.get("usageLimit")),
        "reset_at": row.get("nextDateReset") or d.get("nextDateReset"),
        "days_until_reset": d.get("daysUntilReset"),
        "plan": sub.get("subscriptionTitle"),
        "overage_status": over.get("overageStatus"),
        "overage_cap": row.get("overageCapWithPrecision", row.get("overageCap")),
        "overage_rate": row.get("overageRate"),
        "overage_now": row.get("currentOveragesWithPrecision", row.get("currentOverages")),
        # Which account the figures belong to, without keeping the profile ARN itself (#36).
        "account": A.credit_account(arn),
    }


USAGE = {"usage-limits": usage_limits}
