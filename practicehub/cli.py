"""practicehub: set up a practice, serve it, run its agents, check its track.

    practicehub init --pack general-practice --demo     a new hub in ./state with the pack (and demo patients)
    practicehub serve --dev                             web + API on http://127.0.0.1:8200/
    practicehub portal serve --dev --token-file portal.token   the booking portal on http://127.0.0.1:8210/,
                                                        a client of the hub on 8200 (docs/portal.md)
    practicehub serve --dev --agents                    serve, and let the agents work as soon as work appears
    practicehub agents run                              every active agent works its inbox (in-process)
    practicehub agents run --agent scribe-agent --url http://127.0.0.1:8200 --token-file agent.token
    practicehub token scribe-agent --as dr-amara        issue an API token (an agent's comes from its sponsor)
    practicehub verify                                  check the hash chain and signatures of the track
    practicehub analytics --as rana --days 30          where time goes in each workflow (a policy owner)
    practicehub backup /backups/2026-11-21              a consistent copy of the database, its files and the anchors (not the secret)
    practicehub restore /backups/2026-11-21 --force     put a backup back; then `track restored` records what was lost
    practicehub export --as rana --type Patient --format csv --out patients.csv   every record of a type (a policy owner)
    practicehub export --as rana --all --out /exports/2026-11-21    the whole hub: one .ndjson per type and a manifest
    practicehub owners recover --grant dr-amara         the server holder grants policy_owner when too few are left
    practicehub owners approve-switch-off --member rana the server holder approves a switch-off nobody else can
    practicehub workflow export-bpmn referral > referral.bpmn   a workflow as BPMN 2.0, for BPMN tools
    practicehub workflow import-bpmn referral.bpmn --propose --as rana   a BPMN file as a proposed workflow change
    practicehub openfga init --url http://localhost:8081
    practicehub keycloak realm > realm.json

Configuration is by environment, so secrets never sit in files the hub writes:

    PRACTICEHUB_STATE       state directory (default ./state): the secret and the anchors; the data is in MongoDB
    PRACTICEHUB_DATABASE    MongoDB URI, a replica set (default mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true)
    PRACTICEHUB_DATABASE_NAME   the database (default: the one the URI names, else practicehub)
    PRACTICEHUB_SECRET      HMAC key for signatures (default: generated into state/.secret)
    OPENFGA_URL, OPENFGA_TOKEN                      use OpenFGA (after ``openfga init``)
    MEDPLUM_URL, MEDPLUM_CLIENT_ID, MEDPLUM_CLIENT_SECRET   keep records in Medplum
    KEYCLOAK_URL, KEYCLOAK_REALM, KEYCLOAK_CLIENT_SECRET    accept Keycloak tokens (client ``practicehub``)
    PRACTICEHUB_COLLABORA_URL, PRACTICEHUB_WOPI_BASE, PRACTICEHUB_COLLABORA_DISCOVERY_URL   office documents (Collabora)
    PRACTICEHUB_PACKS       where packs live (default: packs/ next to the source)
    OLLAMA_URL, LITELLM_URL, LITELLM_API_KEY, ANTHROPIC_API_KEY, PRACTICEHUB_ALLOW_EXTERNAL_MODELS   model backends
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from .hub import Hub, HubError
from . import pack as packmod


def state_dir(args) -> Path:
    return Path(getattr(args, "state", None) or os.environ.get("PRACTICEHUB_STATE") or "state")


@contextlib.contextmanager
def database():
    """The hub's database (``PRACTICEHUB_DATABASE``, ``PRACTICEHUB_DATABASE_NAME``), closed afterwards."""
    from .db import open_database
    store = open_database()
    try:
        yield store
    finally:
        store.close()


def _has_hub(store) -> bool:
    """Whether a hub was set up in this database: it has settings or a track."""
    return store.settings.exists() or store.events.exists()


def _is_empty(store) -> bool:
    return not store.file_ids() and not any(store[c].exists() for c in store.collections())


def build_hub(args, pack: str | None = None) -> Hub:
    sd = state_dir(args)
    authorizer = records = None
    if os.environ.get("OPENFGA_URL"):
        from .authz import OpenFGAAuthorizer
        with database() as store:
            cfg = store.get_setting("openfga")
        if not cfg:
            raise SystemExit("OPENFGA_URL is set but this hub has no OpenFGA store; run `practicehub openfga init`")
        authorizer = OpenFGAAuthorizer(os.environ["OPENFGA_URL"], cfg["store_id"], cfg["model_id"],
                                       os.environ.get("OPENFGA_TOKEN"))
    if os.environ.get("MEDPLUM_URL"):
        from .records import MedplumRecordStore
        records = MedplumRecordStore(os.environ["MEDPLUM_URL"], os.environ.get("MEDPLUM_CLIENT_ID", ""),
                                     os.environ.get("MEDPLUM_CLIENT_SECRET", ""))
    return Hub(sd, pack=pack, authorizer=authorizer, records=records)


def cmd_init(args):
    sd = state_dir(args)
    with database() as store:
        if _has_hub(store) and getattr(args, "if_new", False) and not args.force:
            print(f"the database {store.name} already holds a hub: nothing to set up")
            return
        if _has_hub(store) and not args.force:
            raise SystemExit(f"the database {store.name} already holds a hub; pass --force to replace it")
        if args.force:
            store.drop_database()
        name = store.name
    hub = build_hub(args, pack=args.pack)
    hub.install(args.pack, demo=args.demo)
    p = hub.practice
    print(f"{p.get('name')} is set up in the database {name} (state in {sd}/) with the {args.pack} pack: "
          f"{len(hub.members())} members, "
          f"{len(hub.workflows())} workflows" + (", demo records and work" if args.demo else "") + ".")
    if args.demo:
        print("Next: `practicehub agents run` to let the agents work, then `practicehub serve --dev`.")


def cmd_serve(args):
    from .web.server import serve
    hub = build_hub(args)
    if not hub.pack:
        raise SystemExit("no practice here yet; run `practicehub init` first")
    oidc = None
    if os.environ.get("KEYCLOAK_URL"):
        from .integrations.keycloak import HUB_CLIENT, KeycloakAuth
        oidc = KeycloakAuth(os.environ["KEYCLOAK_URL"], os.environ.get("KEYCLOAK_REALM", "practice"), HUB_CLIENT,
                            os.environ.get("KEYCLOAK_CLIENT_SECRET", ""))
    if args.dev and args.host not in ("127.0.0.1", "localhost"):
        raise SystemExit("dev sign-in (no password) only listens on 127.0.0.1")
    if not args.dev and not os.environ.get("PRACTICEHUB_SECRET"):
        print("warning: PRACTICEHUB_SECRET is not set, so the key that seals the track and signatures is the file "
              ".secret in the state directory. Set PRACTICEHUB_SECRET from a secret store.")
    # a hub set up before a rule, or grants widened by a reviewed upgrade, can hold a credential whose issuer or
    # sponsor does not reach as far as its holder: end it now, and say which agents need a new sponsor
    reach = hub.reach_sweep(None, "the hub started")
    if reach["credentials_ended"]:
        print(f"credentials ended because whoever issued them does not reach as far: "
              f"{', '.join(reach['credentials_ended'])}")
    for row in reach["sponsors_out_of_reach"]:
        print(f"warning: {hub.member(row['id'])['name']}'s sponsor does not reach what it does "
              f"({', '.join(row['beyond'])}): a member admin names a sponsor who does (practicehub member sponsor)")
    hub.track.anchor()
    if args.job_workers > 0:  # blocks run outside the requests that make them ready (0: where they become ready)
        start_job_loop(hub, args.job_workers, delay=args.job_delay if args.dev else 0.0)
    start_clock(hub, args.tick_interval)
    if args.agents:
        start_agent_loop(hub, args.agent_interval)
    wopi_hosts = tuple(args.wopi_host or ())
    if any(h in ("0.0.0.0", "::", "") for h in wopi_hosts):
        raise SystemExit("--wopi-host names one address (the docker bridge, say), never every address")
    serve(hub, args.host, args.port, dev=args.dev, oidc=oidc, wopi_hosts=wopi_hosts)


def cmd_portal_serve(args):
    """The portal for the people the practice serves: its own process, apart from the staff hub. It opens no
    database and holds no hub secret: it asks the hub (``--hub``, its private address) with a portal-scoped token of
    the practice's ``[portal] member`` (``practicehub token <member> --as <sponsor> --portal``)."""
    from .web.portal_app import serve as serve_portal
    token = os.environ.get("PRACTICEHUB_PORTAL_TOKEN") or (
        Path(args.token_file).read_text().strip() if args.token_file else "")
    if not token:
        raise SystemExit("give the portal's token in PRACTICEHUB_PORTAL_TOKEN or --token-file (a token of the "
                         "[portal] member with --portal; see docs/portal.md)")
    serve_portal(args.hub, token, args.host, args.port, dev=args.dev, public_origin=args.public_origin)


def portal_sweep(hub) -> dict:
    """The portal's spent codes (their messages withdrawn), ended sessions, old counts and answers, cleared."""
    from .portal import Portal, config
    if not (hub.pack and config(hub.pack)):
        return {}
    try:
        return Portal(hub).sweep()
    except ValueError:  # a [portal] the hub refuses: nothing of it to sweep but what is there
        return {}


def start_clock(hub, interval: float = 60.0):
    """While serving, run what time makes due (scheduled work, overdue items) and anchor the track, every
    ``interval`` seconds. ``practicehub tick`` from cron does the same for a hub that is not served."""
    import threading
    import time

    def loop():
        while True:
            time.sleep(interval)
            try:
                hub.sync_modules()  # modules the command line installed, upgraded or removed meanwhile
                hub.engine.tick()
                hub.outbox_tick()
                portal_sweep(hub)
                hub.track.anchor()
            except Exception as exc:  # noqa: BLE001 - the clock keeps going; the problem is reported
                print(f"tick failed: {exc}")

    if interval > 0:
        threading.Thread(target=loop, name="practicehub-clock", daemon=True).start()


def start_agent_loop(hub, interval: float = 30.0):
    """Run every active agent in this process, as soon as new work appears (an injected prompt, a new step) and every
    ``interval`` seconds. The agents act as themselves, through the same calls the API makes."""
    import threading
    import time

    from .agents.runner import run_all_local

    def loop():
        while True:
            hub.agents_wanted.wait(interval)
            hub.agents_wanted.clear()
            time.sleep(0.2)  # let the request that made the work finish its transaction
            try:
                hub.sync_modules()
                n = run_all_local(hub, rounds=10, log=lambda m: print(f"[agents] {m}", flush=True))
                if n:
                    print(f"[agents] handled {n} item(s)", flush=True)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive; the hub has already refused what it should
                print(f"[agents] stopped a round: {exc}", flush=True)

    t = threading.Thread(target=loop, name="agents", daemon=True)
    t.start()
    return t


def start_job_loop(hub, workers: int = 3, interval: float = 5.0, delay: float = 0.0):
    """Run block automations in this process, outside the requests that make them ready (``Engine.jobs``): from now
    on the hub queues them (``Hub.jobs_async``), and ``workers`` threads take them as they are queued and every
    ``interval`` seconds. The first worker takes only python blocks (the sandbox: quick), so a screen that decides
    whether someone is paged never waits behind a slow engine. ``delay``: seconds each job waits before it runs,
    to watch one go by in ``--dev``."""
    import threading

    hub.jobs_async = True
    hub.engine.job_delay = delay
    try:
        cut = hub.engine.recover_jobs(startup=True)
        if cut:
            print(f"[jobs] {cut} job(s) were cut short when the hub last stopped", flush=True)
    except Exception as exc:  # noqa: BLE001 - the hub still serves; the tick tries again
        print(f"[jobs] could not look for jobs cut short: {exc}", flush=True)

    def loop(lane):
        while True:
            hub.jobs_wanted.wait(interval)
            hub.jobs_wanted.clear()
            try:
                hub.sync_modules()
                hub.engine.run_jobs(lane)
            except Exception as exc:  # noqa: BLE001 - keep the worker alive; a job cut short is found by the tick
                print(f"[jobs] a worker stopped a round: {exc}", flush=True)
            if hub.engine.jobs(lane):
                hub.jobs_wanted.set()  # more came in meanwhile (another worker cleared the flag)

    threads = [threading.Thread(target=loop, args=("local" if n == 0 and workers > 1 else None,),
                                name=f"jobs-{n}", daemon=True) for n in range(max(1, workers))]
    for t in threads:
        t.start()
    return threads


def cmd_agents_run(args):
    from .agents.runner import AgentRunner, HttpClient, LocalClient, Refused, run_all_local
    from .models import ModelGateway

    def log(msg):
        print(msg, flush=True)

    if args.url:
        if not args.agent:
            raise SystemExit("--url needs --agent (each agent runs with its own token)")
        token = os.environ.get("PRACTICEHUB_TOKEN") or (Path(args.token_file).read_text().strip() if args.token_file else "")
        if not token:
            raise SystemExit("give the agent's token in PRACTICEHUB_TOKEN or --token-file")
        hub = build_hub(args) if _local_hub() else None
        gateway = ModelGateway(hub)
        runner = AgentRunner(HttpClient(args.url, token), gateway, log)
        if args.once:
            log(f"handled {runner.work_once()} item(s)")
        else:
            runner.run_forever(args.interval)
        return
    hub = build_hub(args)
    if args.agent:
        runner = AgentRunner(LocalClient(hub, args.agent), hub.models, log)
        try:
            if args.once:
                log(f"handled {runner.work_once()} item(s)")
            else:
                runner.run_forever(args.interval)
        except Refused as exc:
            raise SystemExit(f"stopped: {exc}") from None
        return
    if args.once:
        n = run_all_local(hub, rounds=10, log=log)
        log(f"agents handled {n} item(s); what is left is for people")
        return
    import time
    while True:
        run_all_local(hub, rounds=10, log=log)
        time.sleep(args.interval)


def cmd_token(args):
    hub = build_hub(args)
    actor = hub.member(args.as_member)
    if args.mcp:
        from . import mcp
        if not args.client:
            raise SystemExit("an MCP token names its client (--client), one the policy owners named for the servers")
        print(mcp.issue_token(hub, actor, args.member, servers_=[x for x in args.mcp.split(",") if x],
                              client=args.client, runs_in=args.runs_in, label=args.label or ""))
        return
    print(hub.issue_token(actor, args.member, args.label or "cli", scope="portal" if args.portal else "api"))


def cmd_member(args):
    """Add members and change roles, sponsors, profiles and credentials, as a member admin would on the Team page."""
    hub = build_hub(args)
    actor = hub.member(args.as_member)
    if args.sub == "add":
        out = hub.add_member(actor, {"kind": args.kind, "id": args.id, "name": args.name, "title": args.title or "",
                                     "email": args.email or "", "sponsor": args.sponsor, "roles": args.role or []})
        print(f"added {out['name']} ({out['id']}) with roles: {', '.join(out['roles']) or 'none'}")
    elif args.sub == "roles":
        out = hub.set_roles(actor, args.id, args.role or [])
        print(f"{out['name']} holds: {', '.join(out['roles']) or 'no roles'}")
    elif args.sub == "approve-roles":
        out = hub.approve_roles(actor, args.id)
        print(f"{out['name']} holds: {', '.join(out['roles'])}")
    elif args.sub == "sponsor":
        out = hub.set_sponsor(actor, args.id, args.sponsor)
        print(f"{out['name']} is now sponsored by {out['sponsor']}")
    elif args.sub == "revoke":
        hub.revoke_token(actor, args.id, args.credential)
        print("revoked")
    elif args.sub == "passphrase-link":
        if args.id == args.as_member:  # the first member admin, before anyone else can issue them one
            actor = {"id": f"cli:{args.as_member}", "kind": "installer", "name": f"{actor['name']} (command line)"}
        out = hub.issue_setup_code(actor, args.id)
        print(f"one-time passphrase setup code for {args.id} (works once, until {out['expires_at']}; give it to them "
              f"in person or by phone, not by email):\n  {out['code']}\n"
              f"they open /setup on the hub, enter their member id, this code and a passphrase of their own")
    elif args.sub == "channel":
        hub.grant_channel(actor, args.id, args.channel, revoke=args.revoke)
        print(f"{args.id} {'no longer delivers' if args.revoke else 'delivers'} {args.channel} messages")
    elif args.sub == "tokens":
        for t in hub.tokens(actor, args.id):
            print(f"{t['id']}  {'revoked' if t['revoked'] else 'live   '}  {t['created_at'][:16]}  {t['label']}")
    elif args.sub == "list":
        for m in hub.members():
            print(f"{m['id']:22} {m['kind']:8} {m['status']:9} {', '.join(m['roles'])}"
                  + (f"  (sponsor {m['sponsor']})" if m["sponsor"] else ""))
    elif args.sub == "withdraw-roles":
        hub.withdraw_roles(actor, args.id)
        print("withdrawn")
    elif args.sub == "dependents":
        deps = hub.dependents(args.id)
        for d in deps:
            print(f"{d['id']:22} {d['kind']:8} {d['credentials']} live credential(s)"
                  + (f"; nobody else holds {', '.join(d['only_holder_of'])}" if d["only_holder_of"] else ""))
        if not deps:
            print("nothing depends on them")
    elif args.sub in ("disable", "ask-disable"):
        hand = {}
        for pair in args.hand_over or []:
            agent, _, person = pair.partition("=")
            if not person:
                raise SystemExit(f"--hand-over takes agent=person, not {pair!r}")
            hand[agent] = person
        stop = "all" if args.stop_all else (args.stop or [])
        if args.sub == "disable":
            out = hub.set_status(actor, args.id, "disabled", args.reason, hand_over=hand, stop=stop)
            print(f"{out['name']} is switched off")
            if out["roles_left_empty"]:
                print(f"nobody switched on now holds: {', '.join(out['roles_left_empty'])}")
        else:
            hub.request_switch_off(actor, args.id, args.reason, hand_over=hand, stop=stop)
            print(f"asked: a second policy owner approves with practicehub member approve-disable {args.id} "
                  f"--as <another policy owner>")
    elif args.sub == "approve-disable":
        out = hub.approve_switch_off(actor, args.id)
        print(f"{out['name']} is switched off")
    elif args.sub == "withdraw-disable":
        hub.withdraw_switch_off(actor, args.id)
        print("withdrawn")
    elif args.sub == "enable":
        out = hub.set_status(actor, args.id, "active")
        print(f"{out['name']} is switched on")
    elif args.sub == "profile":
        changes = {k: v for k, v in (("model", args.model), ("max_risk", args.max_risk)) if v is not None}
        if args.direct_writes is not None:
            changes["direct_writes"] = args.direct_writes == "yes"
        if args.skill is not None:
            changes["skills"] = args.skill
        out = hub.propose_agent_profile(actor, args.id, changes, args.note or "")
        if out["status"] == "active":  # a pure reduction: it only lets the agent do less
            print(f"version {out['version']} of {args.id}'s profile is in force: it only takes away, so it needs no "
                  f"approval (it is on the track)")
        else:
            print(f"proposed version {out['version']} of {args.id}'s profile; it takes effect when another policy "
                  f"owner approves it: practicehub member approve-profile {args.id} {out['version']} --as <policy owner>")
    elif args.sub == "approve-profile":
        out = hub.approve_agent_profile(actor, args.id, args.version)
        print(f"version {args.version} of {out['name']}'s profile is in force")
    elif args.sub == "withdraw-profile":
        hub.withdraw_agent_profile(actor, args.id, args.version, args.reason or "")
        print("withdrawn")
    elif args.sub == "profile-versions":
        for v in hub.agent_profile_versions(args.id):
            pr = v["profile"]
            print(f"v{v['version']:<3} {v['status']:9} {pr.get('model', ''):14} {pr.get('max_risk', 'low'):6} "
                  f"{', '.join(pr.get('skills', []))}  by {v['author']}"
                  + (f", approved by {v['approved_by']}" if v["approved_by"] else ""))
    warn = hub.member_admin_warning()
    if warn and args.sub in ("list", "roles", "approve-roles", "disable", "approve-disable"):
        print("warning:", warn)
    if args.sub in ("add", "roles") and hub.role_request(args.id):
        print("a role that approves changes or manages members, or gives an agent or service restricted data, "
              "waits for a second policy owner: "
              f"practicehub member approve-roles {args.id} --as <another policy owner>")


def cmd_verify(args):
    hub = build_hub(args)
    v = hub.verify()
    if v["ok"]:
        hub.track.anchor()
        print(f"track verifies: {v['events']} events, chain, seals and signatures intact; head {v['head']['seq']} "
              f"{v['head']['hash'][:16]} (keep a copy of the head, or of {hub.track.anchors or 'the anchors'}, "
              f"off this machine)")
        return
    print(f"track does NOT verify ({v['events']} events):")
    for p in v["problems"]:
        print("  -", p)
    raise SystemExit(1)


def _anchors_path(sd: Path) -> Path:
    return Path(os.environ.get("PRACTICEHUB_ANCHORS") or sd / "track.anchors")


def _local_hub() -> bool:
    """Whether this machine reaches a hub's database (an agent working through the API may have none)."""
    try:
        with database() as store:
            return _has_hub(store)
    except Exception:  # noqa: BLE001 - no database here: the agent works without the hub's model settings
        return False


BACKUP_FORMAT = "practicehub-mongodb/1"
_SAFE_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _json_options():
    from bson import json_util
    return json_util.CANONICAL_JSON_OPTIONS


def _dump(store, dest: Path) -> dict:
    """Write the whole database into ``dest``: each collection as JSON Lines (MongoDB canonical extended JSON, so
    every type comes back as it was), every file under ``files/`` with ``files/index.json``, and ``backup.json``
    last, so a backup that stopped half way has no manifest and is never restored. The collections are read from one
    snapshot, so a hub that is serving is copied consistently. Returns the manifest."""
    from bson import json_util
    from .db import utcnow
    opts = _json_options()
    dest.mkdir(parents=True, exist_ok=True)
    counts, head = {}, None
    with store.client.start_session(snapshot=True) as s:
        for name in store.collections():
            n = 0
            with open(dest / f"{name}.jsonl", "w", encoding="utf-8") as fh:
                for doc in store.db[name].find({}, session=s).sort("_id", 1):
                    fh.write(json_util.dumps(doc, json_options=opts) + "\n")
                    n += 1
            counts[name] = n
        head = store.db["events"].find_one({}, {"seq": 1, "hash": 1}, sort=[("seq", -1)], session=s)
    # files are content-addressed and written outside transactions: one newer than the snapshot is harmless
    (dest / "files").mkdir(exist_ok=True)
    index = []
    for i, fid in enumerate(store.file_ids()):
        got = store.get_file(fid)
        if got is None:
            continue
        data, meta = got
        fname = fid if isinstance(fid, str) and _SAFE_NAME.fullmatch(fid) else f"file-{i:06d}"
        (dest / "files" / fname).write_bytes(data)
        index.append({"id": fid, "file": fname, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "metadata": meta})
    (dest / "files" / "index.json").write_text(json_util.dumps(index, json_options=opts, indent=2))
    manifest = {"format": BACKUP_FORMAT, "at": utcnow(), "database": store.name,
                "head": {"seq": head["seq"], "hash": head["hash"]} if head else {"seq": 0, "hash": ""},
                "collections": counts, "files": len(index)}
    return manifest


def _load(store, src: Path, manifest: dict) -> dict:
    """Load a backup written by ``_dump`` into an empty database. Returns the restored head of the track."""
    from bson import json_util
    opts = _json_options()
    for name, want in sorted((manifest.get("collections") or {}).items()):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise SystemExit(f"the backup names a collection {name!r} this hub does not have")
        coll, batch, n = store.db[name], [], 0
        with open(src / f"{name}.jsonl", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                batch.append(json_util.loads(line, json_options=opts))
                if len(batch) >= 1000:
                    coll.insert_many(batch, ordered=True)
                    n, batch = n + len(batch), []
        if batch:
            coll.insert_many(batch, ordered=True)
            n += len(batch)
        if n != want:
            raise SystemExit(f"the backup's {name} has {n} documents, its manifest says {want}: it is damaged")
    index_file = src / "files" / "index.json"
    index = json_util.loads(index_file.read_text(), json_options=opts) if index_file.exists() else []
    for e in index:
        data = (src / "files" / e["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != e.get("sha256"):
            raise SystemExit(f"the backup's file {e['id']} does not match its checksum: it is damaged")
        store.put_file(e["id"], data, e.get("metadata") or {})
    head = store.events.find_one(sort=[("seq", -1)], fields=["seq", "hash"])
    got = {"seq": head["seq"], "hash": head["hash"]} if head else {"seq": 0, "hash": ""}
    if got != manifest.get("head"):
        raise SystemExit(f"the restored track ends at event {got['seq']}, the backup's manifest at "
                         f"{(manifest.get('head') or {}).get('seq')}: the backup is damaged")
    return got


def cmd_backup(args):
    """A consistent copy of the hub's database (every collection and file) and of the anchors file. The secret is not
    copied: keep it in your secret store. A backup without the anchors of the hub that wrote it cannot show that
    nothing was taken out."""
    import shutil
    sd, dest = state_dir(args), Path(args.dest)
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest}/ is not empty; back up into a new directory")
    with database() as store:
        if not _has_hub(store):
            raise SystemExit(f"there is no hub in the database {store.name}")
        manifest = _dump(store, dest)
    anchors = _anchors_path(sd)
    if anchors.exists():
        shutil.copyfile(anchors, dest / "track.anchors")
    manifest["anchors"] = anchors.exists()
    (dest / "backup.json").write_text(json.dumps(manifest, indent=2))
    head = manifest["head"]
    print(f"backed up to {dest}/: the track up to event {head['seq']}, {sum(manifest['collections'].values())} "
          f"documents and {manifest['files']} file(s)" + (" and its anchors" if anchors.exists() else "")
          + ". The secret is not in it: keep PRACTICEHUB_SECRET (or state/.secret) in your secret store.")


def cmd_restore(args):
    """Put a backup's database back. The anchors and the secret stay as they are: the anchors still name the events
    the backup does not have, so verify reports them until a policy owner records the loss with ``track restored``."""
    import time
    sd, src = state_dir(args), Path(args.src)
    if not (src / "backup.json").exists():
        raise SystemExit(f"there is no backup in {src}/ (no backup.json)")
    manifest = json.loads((src / "backup.json").read_text())
    if manifest.get("format") != BACKUP_FORMAT:
        raise SystemExit(f"{src}/ is not a backup this hub can restore (format {manifest.get('format')!r})")
    with database() as store:
        if not _is_empty(store):
            if not args.force:
                raise SystemExit(f"the database {store.name} is not empty; stop the hub, then pass --force (the "
                                 f"current one is backed up beside the state first)")
            kept = sd / f"before-restore-{time.strftime('%Y%m%d-%H%M%S')}"
            before = _dump(store, kept)
            before["anchors"] = False
            (kept / "backup.json").write_text(json.dumps(before, indent=2))
            print(f"the current database is kept in {kept}/ (restore it like any backup)")
            store.drop_database()
    sd.mkdir(parents=True, exist_ok=True)
    with database() as store:  # opened again: its indexes are made before the documents go in
        head = _load(store, src, manifest)
        name = store.name
    print(f"restored the database {name} from {src}/: the track up to event {head['seq']}. Next, a policy owner "
          f"records what was lost: practicehub track restored --as <policy owner>, then practicehub verify.")


def cmd_track_restored(args):
    sd = state_dir(args)
    if not os.environ.get("PRACTICEHUB_SECRET") and not (sd / ".secret").exists():
        raise SystemExit("recording a restore needs the secret that sealed the track: set PRACTICEHUB_SECRET")
    hub = build_hub(args)
    seq = hub.record_restore(_person(hub, args.as_member), args.note or "")
    ev = hub.track.get(seq)
    lost = ev["data"].get("lost_through")
    print(f"recorded in the track (event {seq}): the anchors vouch for events up to {ev['data']['lost_after']}; "
          f"the ones after it that the backup did not have were lost"
          + (f", up to event {lost} at least" if lost else "") + "; the anchors hold the track from here.")


def cmd_owners_recover(args):
    sd = state_dir(args)
    if not os.environ.get("PRACTICEHUB_SECRET") and not (sd / ".secret").exists():
        raise SystemExit("recovering a policy owner needs the secret that sealed the track: set PRACTICEHUB_SECRET")
    hub = build_hub(args)
    out = hub.recover_policy_owner(args.grant, args.reason or "")
    print(f"{out['name']} is a policy owner again (role {out['role']}); the track records that the server holder did it")


def cmd_owners_approve_switch_off(args):
    sd = state_dir(args)
    if not os.environ.get("PRACTICEHUB_SECRET") and not (sd / ".secret").exists():
        raise SystemExit("approving as the server holder needs the secret that sealed the track: set PRACTICEHUB_SECRET")
    hub = build_hub(args)
    out = hub.holder_approves_switch_off(args.member)
    print(f"{out['name']} is switched off; the track records that the server holder approved it")
    if hub.policy_owner_count() < hub.MIN_POLICY_OWNERS:
        print("Fewer than two policy owners are left, so nothing can be approved: "
              "practicehub owners recover --grant <person>")
    if not hub.member_admins():
        print("Nobody switched on manages members now (adds people, changes roles, switches someone off) until two "
              "policy owners give someone that role: one asks, the other approves.")
    if out.get("roles_left_empty"):
        names = [hub.role(r)["name"] if hub._role_known(r) else r for r in out["roles_left_empty"]]
        print(f"Nobody switched on now holds: {', '.join(names)}")
    for aid in out.get("stopped") or []:
        print(f"{hub.member(aid)['name']} stopped with them: it needs a new sponsor (practicehub member sponsor) and "
              f"new credentials from that sponsor before it runs again")
    for aid, sp in (out.get("handed_over") or {}).items():
        print(f"{hub.member(aid)['name']} is now sponsored by {hub.member(sp)['name']}")


def cmd_module_list(args):
    from . import pack as packmod
    hub = build_hub(args)
    for mid, why in hub.module_problems.items():
        print(f"{mid:12} {'refused':10} {why}")
    for d in sorted(packmod.MODULES_DIR.glob("*/module.toml")):
        m = packmod.load_module(d.parent.name, run_code=False)
        st = hub.module_status(m.id)["state"]
        mark = {"running": "installed", "not_running": "not run"}.get(st, "available")
        needs = f" (needs {', '.join(m.requires)})" if m.requires else ""
        print(f"{m.id:12} {mark:10} {m.practice.get('name', m.id)}: {m.practice.get('summary', '')}{needs}")


LIVE_HINT = ("a running hub on this database picks the change up on its next request (no restart needed); its modules "
             "page says if it could not")


def cmd_module_install(args):
    hub = build_hub(args)
    actor = hub.member(args.as_member)
    if not actor:
        raise SystemExit(f"there is no member {args.as_member}")
    out = hub.install_module(actor, args.name)
    if args.demo:
        m = next(x for x in hub.pack.modules if x.id == out["module"])
        fn = getattr(m.module, "demo", None) if m.module else None
        if fn:
            fn(hub)
    print(f"installed {out['module']} version {out['version']}" + (" with its demo data" if args.demo else ""))
    print(LIVE_HINT)


def _person(hub, mid):
    try:
        return hub.member(mid)
    except Exception:
        raise SystemExit(f"there is no member {mid}") from None


def cmd_export(args):
    """A bulk export, for a policy owner: every record of one type (``--type``), or of every type (``--all``), into
    files. Each record is a read on the track (how=export) and the export is an event (records.exported)."""
    from . import export
    hub = build_hub(args)
    me = _person(hub, args.as_member)
    out = Path(args.out)
    if args.all:
        files, manifest = export.everything(hub, me, args.format or "ndjson")
        out.mkdir(parents=True, exist_ok=False)
        for name, body in files.items():
            (out / name).write_bytes(body)
        n = sum(x["count"] for x in manifest["output"])
        left = sum(x["count"] for x in manifest["left_out"])
        print(f"exported {n} records of {len(manifest['output'])} types into {out}/ (manifest.json lists them)"
              + (f"; {left} records you may not read were left out" if left else ""))
        return
    rt = hub.record_type(args.type)
    ext = out.suffix.lstrip(".").lower()  # the format the file's name says, else FHIR's for a FHIR type
    fmt = args.format or (ext if ext in ("ndjson", "csv", "ods") else "ndjson" if export.is_fhir(rt) else "csv")
    x = export.records_list(hub, me, args.type, fmt, every=True)
    if out.is_dir():
        out = out / x.filename
    if out.exists():
        raise SystemExit(f"{out} exists; name a new file")
    out.write_bytes(x.body)
    print(f"exported {x.count} {args.type} records as {fmt} into {out}")


def cmd_module_review(args):
    hub = build_hub(args)
    out = hub.approve_module_code(_person(hub, args.as_member), args.name)
    print(f"reviewed {out['module']} {out['version']} ({out['digest'][:12]}): another policy owner can install or "
          f"upgrade it now")


def cmd_pack_status(args):
    hub = build_hub(args)
    p = packmod.load(hub.db.get_setting("pack"), run_code=False)
    force = hub._pack_in_force() or {}
    print(f"practice pack {p.id} {p.version}: on disk {p.digest[:12]}, in force {(force.get('digest') or '?')[:12]}")
    if hub.pack_problem:
        print("  " + hub.pack_problem)
    reviewers = hub.pack_reviewers(p.digest)
    if p.digest != force.get("digest"):
        print("  reviewed by " + ", ".join(reviewers) if reviewers else "  not reviewed yet")


def cmd_pack_review(args):
    hub = build_hub(args)
    out = hub.review_pack(_person(hub, args.as_member))
    print(f"reviewed the practice pack {out['pack']} {out['version']} ({out['digest'][:12]}): another policy owner "
          f"can upgrade to it now")


def cmd_pack_upgrade(args):
    hub = build_hub(args)
    out = hub.upgrade_pack(_person(hub, args.as_member))
    print(f"the practice pack {out['pack']} {out['version']} is in force; restart the hub to run it")
    for label in out.get("added", []):
        print(f"  added, active: {label}")
    for d in out.get("proposed", []):
        print(f"  proposed: {d['kind']} {d['id']} version {d['version']} (a policy owner other than you approves it; "
              f"if the practice had changed it, compare before approving)")


def cmd_module_plan(args):
    hub = build_hub(args)
    plan = hub.module_plan(args.name)
    print(f"{plan['module']} {plan['version']}: pack.py {'changed' if plan['code_changed'] else 'unchanged'}; "
          + (f"reviewed by {', '.join(plan['reviewed_by'])}" if plan["reviewed_by"] else
             "needs a review by a policy owner" if plan["needs_review"] else "as installed"))
    for key, what in (("clashes", "refused: not this module's"), ("new", "added, active"),
                      ("changed", "proposed for approval"), ("kept", "proposed: the practice had changed it"),
                      ("pending", "already proposed, waiting"), ("removed", "retired: no longer in the module"),
                      ("roles", "new role"), ("data_classes", "new data class"),
                      ("grants_added", "grant added"), ("grants_removed", "grant taken back")):
        for x in plan[key]:
            print(f"  {what:40} {' '.join(x) if isinstance(x, list) else x}")


def cmd_module_upgrade(args):
    hub = build_hub(args)
    out = hub.upgrade_module(_person(hub, args.as_member), args.name)
    print(f"upgraded {out['module']} to {out['version']}")
    print(LIVE_HINT)
    for p in out["proposed"]:
        print(f"  proposed {p['kind']} {p['id']} v{p['version']}: another policy owner approves it")


def cmd_module_remove(args):
    hub = build_hub(args)
    out = hub.uninstall_module(_person(hub, args.as_member), args.name, args.reason or "")
    print(f"removed {out['module']}; its records stay, readable")
    print(LIVE_HINT)


def cmd_tick(args):
    hub = build_hub(args)
    out = hub.engine.tick()
    hub.engine.run_jobs()  # a job still in the queue (no served hub took it) runs now
    late = hub.outbox_tick()
    swept = portal_sweep(hub)
    hub.track.anchor()
    print(f"started {len(out['started'])} scheduled run(s); flagged {len(out['overdue'])} overdue item(s); "
          f"escalated {len(late)} undelivered message(s)"
          + (f"; cleared {sum(swept.values())} spent portal code(s), session(s) and count(s)" if swept else ""))


def cmd_analytics(args):
    """Process analytics for a policy owner: counts and times per workflow and step, never who the work is about."""
    from . import analytics
    hub = build_hub(args)
    rep = analytics.report(hub, _person(hub, args.as_member), days=args.days)
    print(json.dumps(rep, indent=1) if args.json else "\n".join(analytics.lines(rep)))


def cmd_workflow_export_bpmn(args):
    """The workflow in force as BPMN 2.0 XML (with a diagram), for Camunda Modeler, bpmn.io and other BPMN tools."""
    from . import bpmn
    hub = build_hub(args)
    wf, _ = hub.workflow(args.id)
    xml = bpmn.export_bpmn(wf)
    if args.out:
        Path(args.out).write_text(xml, encoding="utf-8")
        print(f"wrote {args.id} as BPMN to {args.out}")
    else:
        sys.stdout.write(xml)


def cmd_workflow_import_bpmn(args):
    """A BPMN file exported from Practice Hub as workflow TOML on stdout (what its diagram holds that is not imported
    on stderr); with --propose, proposed as a change for a policy owner who did not write it to approve. A diagram
    from another tool is refused, with the tasks it holds listed by lane. Nothing is installed directly."""
    from . import bpmn
    path = Path(args.file)
    if path.stat().st_size > bpmn.MAX_BYTES:
        raise SystemExit(f"error: {path} is too large for a workflow")
    hub = build_hub(args) if args.propose else None
    try:
        out = bpmn.import_bpmn(path.read_bytes(), hub.pack if hub else None)
    except bpmn.BpmnError as exc:
        raise SystemExit("error: " + "\n".join(exc.problems)) from None  # a refusal is several lines: one each
    for p in out.problems:
        print(f"note: {p}", file=sys.stderr)
    if not args.propose:
        sys.stdout.write(out.source)
        return
    if not args.as_member:
        raise SystemExit("error: --propose needs --as <member>, who proposes it")
    res = hub.propose_workflow(_person(hub, args.as_member), out.source, args.note or f"Imported from {path.name}")
    print(f"proposed {res['workflow_id']} v{res['version']}: a policy owner other than {args.as_member} approves it "
          f"before it is used")


def cmd_openfga_init(args):
    import urllib.request
    from .authz import OpenFGAAuthorizer, model_json
    url = args.url.rstrip("/")
    tok = os.environ.get("OPENFGA_TOKEN")
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {tok}"} if tok else {})}

    def post(path, body):
        req = urllib.request.Request(url + path, data=json.dumps(body).encode(), method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    local = Hub(state_dir(args))
    if not local.pack:
        raise SystemExit("run `practicehub init` first; the local tuples are copied into the new store")
    name = local.practice.get("id", "practice")
    store = post("/stores", {"name": f"practicehub-{name}"})
    model = post(f"/stores/{store['id']}/authorization-models", model_json())
    fga = OpenFGAAuthorizer(url, store["id"], model["authorization_model_id"], tok)
    tuples = local.authz.read()
    fga.write(tuples)
    local.db.set_setting("openfga", {"store_id": store["id"], "model_id": model["authorization_model_id"]})
    print(f"OpenFGA store {store['id']} with {len(tuples)} tuples. Set OPENFGA_URL={url} to use it.")


def cmd_keycloak_realm(args):
    from .integrations.keycloak import realm_export
    hub = build_hub(args)
    print(json.dumps(realm_export(hub, args.realm, args.redirect), indent=2))


def cmd_contract_export(args):
    """The core's data contract (Avro), and, given a pack or module, how its types extend the core's."""
    from . import contract, schema
    from .pack import find
    types = []
    if args.pack:
        try:
            d = find(args.pack, "practice")
        except FileNotFoundError:
            d = find(args.pack, "module")
        for f in sorted((d / "types").glob("*.toml")):
            types.append(schema.parse(f.read_text()))
    print(json.dumps(contract.export(types), indent=2))


def cmd_mcp(args):
    """A stdio bridge to one of the hub's MCP servers, for clients that start a command: JSON-RPC lines in on stdin,
    POSTed to ``<url>/mcp/<server>`` with the member's token (from an environment variable, never an argument), and
    the replies out on stdout. It keeps nothing but the session id the hub gave it."""
    from . import mcp_bridge
    token = os.environ.get(args.token_env, "")
    if not token:
        raise SystemExit(f"set {args.token_env} to a member's token (ph_...)")
    raise SystemExit(mcp_bridge.run(f"{args.url.rstrip('/')}/mcp/{args.server}", token, sys.stdin, sys.stdout,
                                    ping=args.ping))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="practicehub", description="A practice platform where agents are members.")
    ap.add_argument("--state", help="state directory (default $PRACTICEHUB_STATE or ./state)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="set up a practice from a pack")
    p.add_argument("--pack", default="general-practice")
    p.add_argument("--demo", action="store_true", help="add the pack's demo records and start some work")
    p.add_argument("--force", action="store_true", help="replace an existing hub")
    p.add_argument("--if-new", action="store_true",
                   help="do nothing if the database already holds a hub (for a container's first start)")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("serve", help="serve the web pages and the API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8200)
    p.add_argument("--dev", action="store_true", help="sign in as any member without a token (local only)")
    p.add_argument("--wopi-host", action="append", metavar="ADDRESS",
                   help="also listen here, for Collabora's WOPI calls only (e.g. the docker bridge, 172.17.0.1)")
    p.add_argument("--agents", action="store_true", help="also run the agents in this process, as work appears")
    p.add_argument("--agent-interval", type=float, default=30.0, help="seconds between agent rounds when idle")
    p.add_argument("--tick-interval", type=float, default=60.0, help="seconds between ticks (scheduled work, overdue items); 0 turns it off")
    p.add_argument("--job-workers", type=int, default=3,
                   help="threads that run block automations outside the requests that make them ready; 0 runs "
                        "each where it becomes ready")
    p.add_argument("--job-delay", type=float, default=0.0,
                   help="with --dev: seconds each job waits before it runs, to watch one go by")
    p.set_defaults(fn=cmd_serve)

    po = sub.add_parser("portal", help="the online booking portal for the people the practice serves"
                        ).add_subparsers(dest="sub", required=True)
    p = po.add_parser("serve", help="serve the portal's pages (a client of the hub, meant to face the internet "
                                    "behind a TLS proxy; no database here)")
    p.add_argument("--hub", default="http://127.0.0.1:8200", help="the hub's private address")
    p.add_argument("--token-file", help="the [portal] member's portal token (or PRACTICEHUB_PORTAL_TOKEN)")
    p.add_argument("--public-origin", help="the address people use (https://book.example.ca), when behind a proxy; "
                   "default: [portal] public_origin")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8210)
    p.add_argument("--dev", action="store_true", help="show sign-in codes on screen (local only, never real people; "
                   "the hub must be served with --dev too)")
    p.set_defaults(fn=cmd_portal_serve)

    ag = sub.add_parser("agents", help="run agents").add_subparsers(dest="sub", required=True)
    p = ag.add_parser("run", help="let agents work their inboxes")
    p.add_argument("--agent", help="one agent (default: all, in-process)")
    p.add_argument("--url", help="work through a hub's API instead of in-process")
    p.add_argument("--token-file")
    p.add_argument("--once", action="store_true", help="one pass, then stop")
    p.add_argument("--interval", type=float, default=5.0)
    p.set_defaults(fn=cmd_agents_run)

    p = sub.add_parser("token", help="issue an API token (or, with --mcp, an MCP token) for a member")
    p.add_argument("member")
    p.add_argument("--as", dest="as_member", required=True, help="who is issuing it (the member, or an agent's or service's sponsor)")
    p.add_argument("--label")
    p.add_argument("--mcp", metavar="SERVERS", help="an MCP token for these servers (comma separated), not an API token")
    p.add_argument("--portal", action="store_true", help="a portal token: the [portal] service member's, for the "
                   "portal's own process; it opens /api/portal/ and nothing else")
    p.add_argument("--client", help="with --mcp: the client (AI app or program) that will use it")
    p.add_argument("--runs-in", dest="runs_in", default="", help="with --mcp: optional; must match where the policy "
                   "owners attested the client sends what it reads")
    p.set_defaults(fn=cmd_token)

    mb = sub.add_parser("member", help="members: add, roles, sponsors, credentials").add_subparsers(dest="sub",
                                                                                                    required=True)
    p = mb.add_parser("add", help="add a person, agent or service")
    p.add_argument("id")
    p.add_argument("name")
    p.add_argument("--kind", default="person", choices=["person", "agent", "service"])
    p.add_argument("--title")
    p.add_argument("--email")
    p.add_argument("--sponsor", help="the accountable person (agents and services)")
    p.add_argument("--role", action="append", help="a role to hold (repeat)")
    p = mb.add_parser("roles", help="set the roles a member holds (every one, repeated --role; none clears)")
    p.add_argument("id")
    p.add_argument("--role", action="append")
    p = mb.add_parser("approve-roles", help="a second policy owner approves a requested role change")
    p.add_argument("id")
    p = mb.add_parser("sponsor", help="give an agent or service a new sponsor")
    p.add_argument("id")
    p.add_argument("sponsor")
    p = mb.add_parser("tokens", help="a member's credentials")
    p.add_argument("id")
    p = mb.add_parser("revoke", help="revoke one credential")
    p.add_argument("id")
    p.add_argument("credential")
    p = mb.add_parser("passphrase-link", help="issue a person a one-time code to set their passphrase (a member admin)")
    p.add_argument("id")
    p = mb.add_parser("list", help="every member, and a warning when only one person manages members")
    p = mb.add_parser("withdraw-roles", help="withdraw a waiting role change")
    p.add_argument("id")
    p = mb.add_parser("dependents", help="the agents and services that stop if this person is switched off")
    p.add_argument("id")
    for name, hlp in (("disable", "switch a member off (a member admin, or an agent's sponsor)"),
                      ("ask-disable", "a policy owner asks to switch a member off; a second policy owner approves")):
        p = mb.add_parser(name, help=hlp)
        p.add_argument("id")
        p.add_argument("--reason", required=True)
        p.add_argument("--hand-over", action="append", metavar="AGENT=PERSON",
                       help="give an agent or service they sponsor to a new sponsor (repeat)")
        p.add_argument("--stop", action="append", metavar="AGENT",
                       help="let an agent or service they sponsor stop; its credentials end for good (repeat)")
        p.add_argument("--stop-all", action="store_true", help="let every one not handed over stop")
    p = mb.add_parser("approve-disable", help="a second policy owner approves switching a member off")
    p.add_argument("id")
    p = mb.add_parser("withdraw-disable", help="withdraw or decline a request to switch a member off")
    p.add_argument("id")
    p = mb.add_parser("enable", help="switch a member on again (a member admin)")
    p.add_argument("id")
    p = mb.add_parser("profile", help="propose a new version of an agent's profile (a second policy owner approves)")
    p.add_argument("id")
    p.add_argument("--model")
    p.add_argument("--max-risk", choices=["low", "medium", "high"])
    p.add_argument("--skill", action="append", help="every skill or prompt block it takes (repeat)")
    p.add_argument("--direct-writes", choices=["yes", "no"],
                   help="whether it may change records directly, outside the work it is given (no, unless approved)")
    p.add_argument("--note")
    p = mb.add_parser("approve-profile", help="a policy owner other than its author puts a proposed profile in force")
    p.add_argument("id")
    p.add_argument("version", type=int)
    p = mb.add_parser("withdraw-profile", help="withdraw or decline a proposed profile")
    p.add_argument("id")
    p.add_argument("version", type=int)
    p.add_argument("--reason")
    p = mb.add_parser("profile-versions", help="every version of an agent's profile")
    p.add_argument("id")
    p = mb.add_parser("channel", help="let a service (a delivery connector) deliver on an outbox channel")
    p.add_argument("id")
    p.add_argument("channel")
    p.add_argument("--revoke", action="store_true", help="stop it delivering on the channel")
    for p in mb.choices.values():
        p.add_argument("--as", dest="as_member", required=True)
        p.set_defaults(fn=cmd_member)
    p = sub.add_parser("export", help="export every record of a type, or the whole hub, to files (a policy owner)")
    p.add_argument("--as", dest="as_member", required=True, help="the policy owner making the export")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--type", help="every record of this type")
    g.add_argument("--all", action="store_true", help="every record of every type, one file per type")
    p.add_argument("--format", choices=["ndjson", "csv", "ods"], help="ndjson (FHIR Bulk Data; the default "
                   "for a FHIR type), csv (the default otherwise), ods; --all takes ndjson or csv")
    p.add_argument("--out", required=True, help="the file to write (a directory with --all)")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("verify", help="verify the track")
    p.set_defaults(fn=cmd_verify)
    tr = sub.add_parser("track", help="the track itself").add_subparsers(dest="sub", required=True)
    p = tr.add_parser("restored", help="after a restore: record that the events after the backup were lost, and "
                                       "anchor again (a policy owner, with the secret)")
    p.add_argument("--as", dest="as_member", required=True)
    p.add_argument("--note")
    p.set_defaults(fn=cmd_track_restored)
    p = sub.add_parser("backup", help="copy the database, its files and the anchors (not the secret) into a new "
                                      "directory")
    p.add_argument("dest")
    p.set_defaults(fn=cmd_backup)
    p = sub.add_parser("restore", help="put a backup's database back (stop the hub first)")
    p.add_argument("src")
    p.add_argument("--force", action="store_true", help="replace the current database (it is backed up into the "
                                                        "state directory first)")
    p.set_defaults(fn=cmd_restore)
    ow = sub.add_parser("owners", help="policy owners").add_subparsers(dest="sub", required=True)
    p = ow.add_parser("recover", help="the server holder grants policy_owner to a person, when fewer than two "
                                      "active policy owners are left (needs the secret)")
    p.add_argument("--grant", required=True, help="the person to make a policy owner")
    p.add_argument("--reason")
    p.set_defaults(fn=cmd_owners_recover)
    p = ow.add_parser("approve-switch-off", help="the server holder approves a policy owner's request to switch "
                                                 "someone off, when no other policy owner can (needs the secret)")
    p.add_argument("--member", required=True, help="the member a policy owner asked to switch off")
    p.set_defaults(fn=cmd_owners_approve_switch_off)

    pk = sub.add_parser("pack", help="the practice pack: its code and settings, under change control"
                        ).add_subparsers(dest="sub", required=True)
    p = pk.add_parser("status", help="whether the pack on disk is the one in force")
    p.set_defaults(fn=cmd_pack_status)
    p = pk.add_parser("review", help="sign that you reviewed the practice pack on disk: a second policy owner then "
                                     "upgrades to it")
    p.add_argument("--as", dest="as_member", required=True)
    p.set_defaults(fn=cmd_pack_review)
    p = pk.add_parser("upgrade", help="put the reviewed practice pack on disk into force")
    p.add_argument("--as", dest="as_member", required=True)
    p.set_defaults(fn=cmd_pack_upgrade)
    mo = sub.add_parser("module", help="document control, quality and other modules").add_subparsers(dest="sub",
                                                                                                   required=True)
    p = mo.add_parser("list", help="the modules this hub can install")
    p.set_defaults(fn=cmd_module_list)
    p = mo.add_parser("install", help="install a module; a policy owner does it, and the track records who")
    p.add_argument("name")
    p.add_argument("--as", dest="as_member", required=True, help="the policy owner installing it")
    p.add_argument("--demo", action="store_true", help="also load the module's demo data")
    p.set_defaults(fn=cmd_module_install)
    p = mo.add_parser("review", help="sign that you reviewed a module on disk (code and definitions): a second policy "
                                     "owner then installs or upgrades it")
    p.add_argument("name")
    p.add_argument("--as", dest="as_member", required=True)
    p.set_defaults(fn=cmd_module_review)
    p = mo.add_parser("plan", help="how the module on disk differs from what the practice runs")
    p.add_argument("name")
    p.set_defaults(fn=cmd_module_plan)
    p = mo.add_parser("upgrade", help="run the module's new code; new definitions arrive, changed ones are proposed")
    p.add_argument("name")
    p.add_argument("--as", dest="as_member", required=True)
    p.set_defaults(fn=cmd_module_upgrade)
    p = mo.add_parser("remove", help="retire a module's definitions and take out what it added; records stay")
    p.add_argument("name")
    p.add_argument("--as", dest="as_member", required=True)
    p.add_argument("--reason")
    p.set_defaults(fn=cmd_module_remove)

    p = sub.add_parser("tick", help="run scheduled workflows and flag overdue work (run it from cron)")
    p.set_defaults(fn=cmd_tick)

    p = sub.add_parser("analytics", help="how work moves: runs, step times, overdue work and the bottleneck, per "
                       "workflow (a policy owner)")
    p.add_argument("--as", dest="as_member", required=True, help="the policy owner reading it")
    p.add_argument("--days", type=int, choices=[7, 30, 90], default=30, help="the period (default 30 days)")
    p.add_argument("--json", action="store_true", help="print JSON, as /api/analytics gives it")
    p.set_defaults(fn=cmd_analytics)

    wk = sub.add_parser("workflow", help="workflows: BPMN export and import").add_subparsers(dest="sub", required=True)
    p = wk.add_parser("export-bpmn", help="print the workflow in force as BPMN 2.0 XML")
    p.add_argument("id")
    p.add_argument("--out", help="write to this file instead of stdout")
    p.set_defaults(fn=cmd_workflow_export_bpmn)
    p = wk.add_parser("import-bpmn", help="read a BPMN file exported from Practice Hub as workflow TOML (stdout), or "
                      "propose it with --propose")
    p.add_argument("file")
    p.add_argument("--propose", action="store_true", help="propose it as a workflow change (never installs it)")
    p.add_argument("--as", dest="as_member", help="the member who proposes it")
    p.add_argument("--note", help="what changes, and why")
    p.set_defaults(fn=cmd_workflow_import_bpmn)

    fg = sub.add_parser("openfga", help="OpenFGA").add_subparsers(dest="sub", required=True)
    p = fg.add_parser("init", help="create a store and model, copy the hub's tuples into it")
    p.add_argument("--url", required=True)
    p.set_defaults(fn=cmd_openfga_init)

    kc = sub.add_parser("keycloak", help="Keycloak").add_subparsers(dest="sub", required=True)
    p = kc.add_parser("realm", help="print a realm import with every member")
    p.add_argument("--realm", default="practice")
    p.add_argument("--redirect", default="http://localhost:8200/*")
    p.set_defaults(fn=cmd_keycloak_realm)

    ct = sub.add_parser("contract", help="the data contract between the core and the packs").add_subparsers(
        dest="sub", required=True)
    p = ct.add_parser("export", help="print the core's base types as Avro schemas (and a pack's extensions of them)")
    p.add_argument("--pack", help="a practice pack or module (name or directory)")
    p.set_defaults(fn=cmd_contract_export)

    p = sub.add_parser("mcp", help="bridge one of the hub's MCP servers to stdio (for clients that start a command)")
    p.add_argument("server", help="core, practice, or an installed module's id")
    p.add_argument("--url", required=True, help="the hub, e.g. https://hub.example.ca")
    p.add_argument("--token-env", default="PRACTICEHUB_TOKEN", help="the environment variable holding the token")
    p.add_argument("--ping", type=float, default=30.0, help="seconds between pings, to hear of tool changes (0: off)")
    p.set_defaults(fn=cmd_mcp)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except HubError as exc:
        # a refusal with several problems says each one (a type that does not parse, a form with three bad fields)
        said = [str(x) for x in (getattr(exc, "problems", None) or []) if str(x) != str(exc)]
        raise SystemExit("\n".join([f"error: {exc}", *("  " + x for x in said)])) from None


if __name__ == "__main__":
    main(sys.argv[1:])
