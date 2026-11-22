"""Process analytics: how work moves through each workflow, as counts and times only.

``report(hub, member, days=30)`` looks at the runs started in the last ``days`` days and gives, per workflow: runs
started, finished, cancelled and still open; how long finished runs took (median and 90th percentile); per step, how
many times it came up, the median time it waited before someone took it, the median time from taken to done, how
often it was overdue and how often it was escalated; the step where most time goes (the bottleneck); the work open
now, by age; and missed deadlines (runs that carry ``deadline_at`` / ``deadline_missed_at``).

It is aggregates only: no record, no title of a record, no inputs, no item or run ids leave it. Workflow and step
titles come from the workflow definitions, which name the work, never who it is about. Only a policy owner may read
it (the same gate as the Track and the Access report).

It reads by index: the runs of the period (at most ``MAX_RUNS``, newest first; ``capped`` says when there were more),
then their items and their events by run, in batches. Open work is read by status (at most ``MAX_OPEN`` items).
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone

PERIODS = (7, 30, 90)
DEFAULT_DAYS = 30
MAX_RUNS = 20000   # runs looked at in one report (newest first)
MAX_OPEN = 20000   # open work items looked at for the ages
BATCH = 500        # run ids per query
OPEN_STATES = ("open", "claimed", "held")
NOT_WORK = ("notice", "hold")  # a notice tells someone something; a hold is a sponsor's check of an agent's answer
AGES = ((86400, "Under a day"), (3 * 86400, "1 to 3 days"), (7 * 86400, "3 to 7 days"), (None, "Over a week"))


def may_see(hub, member) -> bool:
    return bool(member) and hub.is_policy_owner(member)


def _t(stamp) -> datetime | None:
    if not stamp:
        return None
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _secs(a, b) -> float | None:
    return max(0.0, (b - a).total_seconds()) if a and b else None


def median(xs) -> float | None:
    return statistics.median(xs) if xs else None


def p90(xs) -> float | None:
    """The 90th percentile, nearest rank."""
    if not xs:
        return None
    s = sorted(xs)
    return s[max(0, math.ceil(0.9 * len(s)) - 1)]


def duration(secs) -> str:
    """A time span in plain words: 45m, 6h, 2d 3h."""
    if secs is None:
        return "–"
    secs = int(round(secs))
    if secs < 60:
        return "under a minute" if secs else "0m"
    m = secs // 60
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m" if m and h < 10 else f"{h}h"
    d, h = divmod(h, 24)
    return f"{d}d {h}h" if h else f"{d}d"


def bottleneck_words(b: dict | None) -> str:
    if not b:
        return "No step has been finished in this period yet, so there is no bottleneck to show."
    if b["phase"] == "waiting":
        return (f"Most time is spent waiting for ‘{b['title']}’ to be taken: "
                f"median {duration(b['median'])}.")
    return f"Most time is spent doing ‘{b['title']}’ once it is taken: median {duration(b['median'])}."


def _batches(ids: list, n: int = BATCH):
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def _definition(hub, cache: dict, wid: str, version):
    """(title, [(step id, step title)]) from the workflow's definition: the version the runs used, else the one in
    force. Nothing from a run."""
    key = (wid, version)
    if key not in cache:
        wf = None
        for v in (version, None):
            try:
                wf = hub.workflow(wid, v)[0]
                break
            except Exception:  # noqa: BLE001 - a retired or unknown version: try the one in force
                continue
        cache[key] = (wf.title, [(s.id, s.title) for s in wf.steps]) if wf else (None, [])
    return cache[key]


def report(hub, member, *, days: int = DEFAULT_DAYS, now: datetime | None = None, max_runs: int = MAX_RUNS,
           max_open: int = MAX_OPEN) -> dict:
    """The analytics for the last ``days`` days, for a policy owner (``Denied`` otherwise)."""
    from .hub import Denied
    if not may_see(hub, member):
        raise Denied("process analytics are read by a policy owner")
    days = int(days) if int(days) in PERIODS else DEFAULT_DAYS
    if now is None:
        now = datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    since = now - timedelta(days=days)
    db = hub.db
    runs = db.runs.find({"created_at": {"$gte": _stamp(since)}, "version": {"$ne": 0}}, sort=[("created_at", -1)],
                        limit=max_runs + 1,
                        fields=["workflow_id", "version", "status", "created_at", "updated_at", "deadline_at",
                                "deadline_missed_at"])
    capped = len(runs) > max_runs
    runs = runs[:max_runs]
    by_run = {r["_id"]: r for r in runs}
    finished_at: dict = {}
    claimed_at: dict = {}
    handed_up: dict = {}
    items: list = []
    for ids in _batches(list(by_run)):
        for ev in db.events.find({"run_id": {"$in": ids},
                                  "action": {"$in": ["run.completed", "item.claimed", "item.escalated"]}},
                                 fields=["action", "run_id", "subject", "at", "data"]):
            if ev["action"] == "run.completed":
                finished_at.setdefault(ev["run_id"], ev["at"])
            elif ev["action"] == "item.claimed":
                claimed_at.setdefault(ev["subject"], ev["at"])  # the first time it was taken
            else:  # by item and step: one item walks a run's steps
                key = (ev["subject"], (ev.get("data") or {}).get("step"))
                handed_up[key] = handed_up.get(key, 0) + 1
        items += db.items.find({"run_id": {"$in": ids}, "kind": {"$nin": list(NOT_WORK)},
                                "status": {"$ne": "folded"}},
                               fields=["run_id", "step_id", "status", "created_at", "updated_at", "due_at",
                                       "escalated", "history"])
    defs: dict = {}
    flows: dict = {}

    def flow(wid, version):
        if wid not in flows:
            title, steps = _definition(hub, defs, wid, version)
            flows[wid] = {"id": wid, "title": title or "A workflow no longer in use", "started": 0, "finished": 0,
                          "cancelled": 0, "open": 0, "cycle": [], "steps": {}, "order": [s for s, _ in steps],
                          "titles": dict(steps), "with_deadline": 0, "deadline_missed": 0,
                          "ages": [0] * len(AGES), "open_now": 0}
        f = flows[wid]
        if version is not None:  # a step only an older version had keeps its own title
            for sid, title in _definition(hub, defs, wid, version)[1]:
                f["titles"].setdefault(sid, title)
        return f

    for r in runs:
        f = flow(r["workflow_id"], r.get("version"))
        f["started"] += 1
        if r["status"] == "completed":
            f["finished"] += 1
            secs = _secs(_t(r["created_at"]), _t(finished_at.get(r["_id"]) or r.get("updated_at")))
            if secs is not None:
                f["cycle"].append(secs)
        elif r["status"] == "cancelled":
            f["cancelled"] += 1
        elif r["status"] == "running":
            f["open"] += 1
        if r.get("deadline_at"):
            f["with_deadline"] += 1
        if r.get("deadline_missed_at"):
            f["deadline_missed"] += 1

    known_items = {it["_id"] for it in items}
    visits = []  # (item, step, began, first taken, done, due, escalations, open, handed up)
    for it in items:
        subj = f"item:{it['_id']}"
        if it.get("history"):
            # a step's time from the item's step history: each step a person did on this item (a child's step is
            # counted once, on the child; an entry of an item folded in from before counts here)
            for h in it["history"]:
                if h.get("kind") not in ("task", "gate"):
                    continue
                if h.get("item") != it["_id"] and h.get("item") in known_items:
                    continue
                hsub = f"item:{h.get('item') or it['_id']}"
                ended = h.get("done_at") if h.get("outcome") not in (None, "cancelled") else None
                visits.append((it, h["step"], h.get("started_at"), h.get("taken_at") or claimed_at.get(hsub)
                               if hsub != subj else h.get("taken_at"), ended, h.get("due_at"),
                               int(h.get("escalated") or 0), not h.get("done_at") and it["status"] in OPEN_STATES,
                               handed_up.get((subj, h["step"]), 0) + (handed_up.get((hsub, None), 0)
                                                                       if hsub != subj else 0)))
            continue
        visits.append((it, it["step_id"], it["created_at"], claimed_at.get(subj),
                       it["updated_at"] if it["status"] == "done" else None, it.get("due_at"),
                       int(it.get("escalated") or 0), it["status"] in OPEN_STATES,
                       sum(v for (sj, _), v in handed_up.items() if sj == subj)))
    for it, sid, began, taken, ended, due_at, escalated, still_open, handed in visits:
        run = by_run.get(it["run_id"])
        if not run:
            continue
        f = flow(run["workflow_id"], run.get("version"))
        s = f["steps"].setdefault(sid, {"count": 0, "wait": [], "work": [], "total": [], "overdue": 0,
                                        "escalations": 0})
        s["count"] += 1
        created, claim = _t(began), _t(taken)
        done = _t(ended)
        if claim or done:
            s["wait"].append(_secs(created, claim or done))
        if claim and done:
            s["work"].append(_secs(claim, done))
        if done:
            s["total"].append(_secs(created, done))
        due = _t(due_at)
        late = bool(due) and ((done and done > due) or (still_open and now > due))
        if late or escalated:
            s["overdue"] += 1
        s["escalations"] += escalated + handed

    # the work open now, by how long it has been waiting (whenever its run started)
    open_items = db.items.find({"status": {"$in": ["open", "claimed"]}, "kind": {"$nin": list(NOT_WORK)}},
                               sort=[("n", -1)], limit=max_open + 1,
                               fields=["run_id", "created_at", "step_started_at"])
    open_capped = len(open_items) > max_open
    open_items = open_items[:max_open]
    run_flow: dict = {k: (v["workflow_id"], v.get("version")) for k, v in by_run.items()}
    missing = sorted({i["run_id"] for i in open_items if i["run_id"] not in run_flow})
    for ids in _batches(missing):
        for r in db.runs.find({"_id": {"$in": ids}}, fields=["workflow_id", "version"]):
            if r.get("version") != 0:
                run_flow[r["_id"]] = (r["workflow_id"], r.get("version"))
    for it in open_items:
        got = run_flow.get(it["run_id"])
        if not got:
            continue
        f = flow(*got)
        age = _secs(_t(it.get("step_started_at") or it["created_at"]), now) or 0
        f["open_now"] += 1
        f["ages"][next(i for i, (lim, _) in enumerate(AGES) if lim is None or age < lim)] += 1

    out = []
    for f in sorted(flows.values(), key=lambda f: (-f["started"], -f["open_now"], f["title"])):
        steps = []
        known = f["order"] + sorted(set(f["steps"]) - set(f["order"]))
        for sid in known:
            s = f["steps"].get(sid)
            if not s:
                continue
            steps.append({"step": sid, "title": f["titles"].get(sid) or "A step no longer in the workflow",
                          "count": s["count"], "wait_median": median(s["wait"]), "work_median": median(s["work"]),
                          "total_median": median(s["total"]), "done": len(s["total"]), "overdue": s["overdue"],
                          "overdue_rate": s["overdue"] / s["count"] if s["count"] else 0.0,
                          "escalations": s["escalations"]})
        neck = None
        timed = [s for s in steps if s["total_median"] is not None]
        if timed:
            top = max(timed, key=lambda s: s["total_median"])
            waiting = (top["wait_median"] or 0) >= (top["work_median"] or 0)
            neck = {"step": top["step"], "title": top["title"], "total_median": top["total_median"],
                    "phase": "waiting" if waiting else "working",
                    "median": top["wait_median"] if waiting else top["work_median"]}
        out.append({"id": f["id"], "title": f["title"], "started": f["started"], "finished": f["finished"],
                    "cancelled": f["cancelled"], "open": f["open"],
                    "cycle_median": median(f["cycle"]), "cycle_p90": p90(f["cycle"]), "steps": steps,
                    "bottleneck": neck, "open_now": f["open_now"],
                    "open_by_age": [{"age": label, "count": n} for (_, label), n in zip(AGES, f["ages"])],
                    "with_deadline": f["with_deadline"], "deadline_missed": f["deadline_missed"]})
    return {"days": days, "since": _stamp(since), "until": _stamp(now), "runs": len(runs), "capped": capped,
            "max_runs": max_runs, "open_capped": open_capped, "max_open": max_open, "workflows": out}


def lines(rep: dict) -> list[str]:
    """The report as plain text (the command line)."""
    out = [f"Process analytics, last {rep['days']} days ({rep['runs']} runs)"]
    if rep["capped"]:
        out.append(f"  only the newest {rep['max_runs']} runs were counted")
    if rep["open_capped"]:
        out.append(f"  only the newest {rep['max_open']} open items were counted for the ages")
    if not rep["workflows"]:
        out.append("  no work in this period")
    for w in rep["workflows"]:
        out += ["", w["title"],
                f"  runs: {w['started']} started, {w['finished']} finished, {w['cancelled']} cancelled, "
                f"{w['open']} still open",
                f"  time to finish: median {duration(w['cycle_median'])}, 90th percentile {duration(w['cycle_p90'])}",
                f"  {bottleneck_words(w['bottleneck'])}"]
        if w["with_deadline"]:
            out.append(f"  deadlines: {w['deadline_missed']} missed of {w['with_deadline']} runs with one")
        if w["open_now"]:
            out.append("  open now: " + ", ".join(f"{a['age'].lower()} {a['count']}" for a in w["open_by_age"]))
        for s in w["steps"]:
            out.append(f"  - {s['title']}: {s['count']} times; waiting {duration(s['wait_median'])}, "
                       f"doing {duration(s['work_median'])}, overdue {s['overdue']} ({s['overdue_rate']:.0%}), "
                       f"escalations {s['escalations']}")
    return out
