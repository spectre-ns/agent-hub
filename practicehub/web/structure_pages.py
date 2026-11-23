"""The record structure, drawn and edited: every record type as a tree of what is part of what (``part_of``), with
the links people make by hand between them (``[[link]]``) drawn as arcs beside it. Dragging a type onto another makes
it a part of that one; a type's panel changes what it is part of and adds or removes its links. Each change is a
proposal of a new version of that type, approved and signed by a policy owner who did not make it, as any change to
a record type is: the drawing shows what is in force, and marks the types with a change waiting."""

from __future__ import annotations

import html
import re
import tomllib
import urllib.parse

from .. import schema
from .definitions_pages import _tk, _tv, toml_dumps

e = html.escape

ROW, NODE_H, NODE_W, INDENT, PAD = 40, 28, 210, 26, 12


class StructurePages:
    """Mixed into the web App."""

    def structure_routes(self, R):
        R("GET", r"/define/structure", self.page_structure)
        R("POST", r"/define/structure", self.post_structure)

    # ------------------------------------------------------------------------------------------ the model
    def _structure(self) -> dict:
        """Every type, its owner (the first of its part_of targets the practice has), its rows in tree order, and
        the arcs: hand links, and (shown on request) what its reference fields point at."""
        hub = self.hub
        rts = hub.record_types()
        owner: dict[str, str | None] = {}
        for t, rt in rts.items():
            targets = hub.concrete(rt.part_of["target"]) if rt.part_of else []
            owner[t] = next((o for o in targets if o in rts and o != t), None)
        kids: dict[str | None, list[str]] = {}
        for t in rts:  # rts is in the practice's order already
            kids.setdefault(owner[t], []).append(t)
        rows: list[tuple[str, int]] = []
        seen: set[str] = set()

        def walk(t, depth):
            if t in seen:
                return
            seen.add(t)
            rows.append((t, depth))
            for k in kids.get(t, []):
                walk(k, depth + 1)
        for t in kids.get(None, []):
            walk(t, 0)
        for t in rts:  # an ownership loop drawn as it is: its types at the top, never lost
            walk(t, 0)
        pending = {d["id"]: d["proposed"] for d in hub.definitions("type") if d["proposed"]}
        defined = {d["id"] for d in hub.definitions("type")}
        arcs = []
        for t, rt in rts.items():
            for x in rt.links:
                for to in hub.concrete(x["to"]):
                    if to in rts:
                        arcs.append({"from": t, "to": to, "rel": x["rel"], "label": x["label"], "hand": True})
            for f in rt.fields:
                if f["type"] == "reference" and not (rt.part_of and f["name"] == rt.part_of["field"]):
                    for to in hub.concrete(f.get("target") or []):
                        if to in rts and to != t:
                            arcs.append({"from": t, "to": to, "rel": f.get("rel") or f["name"],
                                         "label": f["label"], "hand": False})
        return {"types": rts, "owner": owner, "rows": rows, "pending": pending, "defined": defined, "arcs": arcs,
                "kids": kids}

    @staticmethod
    def _under(st: dict, t: str) -> set[str]:
        """``t`` and every type that is part of it, at any depth (a type is never made part of one of these)."""
        out, todo = set(), [t]
        while todo:
            x = todo.pop()
            if x not in out:
                out.add(x)
                todo += st["kids"].get(x, [])
        return out

    # ------------------------------------------------------------------------------------------ the page
    def page_structure(self, req, problems=None, kept: dict | None = None):
        st = self._structure()
        rts = st["types"]
        sel = str((kept or {}).get("type") or req["query"].get("type") or "")
        sel = sel if sel in rts else ""
        notice = req["query"].get("proposed")
        out = []
        if notice:
            t, _, ver = notice.partition(":")
            out.append(f'<div class="flash success" role="status"><p>Proposed version {e(ver)} of '
                       f'<a href="/define/types/{e(t)}#v{e(ver)}">{e(rts[t].label if t in rts else t)}</a>. It takes '
                       f'effect once a policy owner who did not propose it approves and signs it.</p></div>')
        if problems:
            out.append('<div class="flash error" role="alert"><p>The hub refused the change:</p><ul>'
                       + "".join(f"<li>{e(p)}</li>" for p in problems) + "</ul></div>")
        out.append('<p class="muted">What the practice keeps records of: each type under the one its records are part '
                   'of, and the links people make between records. Drag a type onto another to make it a part of '
                   'that one, or onto <b>Top level</b> to make it stand alone; select a type to change its links. '
                   'Each change is proposed, and takes effect when a policy owner who did not make it approves it.</p>')
        out.append('<div class="st-layout"><div class="st-canvas">'
                   '<div class="st-legend" aria-hidden="true"><span class="st-key part"></span> part of '
                   '<span class="st-key hand"></span> link made by hand '
                   '<label class="st-key-toggle"><input type="checkbox" id="st-fields"> '
                   '<span class="st-key field"></span> reference fields</label></div>'
                   + self._structure_svg(st, sel) + '</div>'
                   + f'<aside class="st-side" aria-label="Selected type">{self._structure_panels(req, st, sel, kept)}'
                   f'</aside></div>')
        out.append(self._structure_dialog(st))
        return self.layout(req, "Record structure", "".join(out),
                           crumbs=[("/define/types", "Record types"), (None, "Structure")],
                           actions='<a class="btn secondary" href="/define/types">List of types</a>')

    def _structure_svg(self, st: dict, sel: str) -> str:
        rts, rows = st["types"], st["rows"]
        at = {t: i for i, (t, _) in enumerate(rows)}
        depth = {t: d for t, d in rows}
        top = ROW  # the first row is the "Top level" drop zone
        y = lambda t: top + at[t] * ROW + (ROW - NODE_H) // 2  # noqa: E731
        maxd = max([d for _, d in rows] or [0])
        rail = PAD + maxd * INDENT + NODE_W + 16
        parts = []
        # what is part of what: an elbow from the owner down its left side to each part
        for t, _ in rows:
            o = st["owner"].get(t)
            if o and o in at:
                x0 = PAD + depth[o] * INDENT + 12
                x1 = PAD + depth[t] * INDENT
                yc = y(t) + NODE_H // 2
                parts.append(f'<path class="st-part" d="M{x0},{y(o) + NODE_H} V{yc} H{x1}"/>')
        arcs = []
        named: set[tuple[str, str]] = set()  # a link is labelled once, on its first arc
        lanes: dict[tuple[str, str], int] = {}
        for a in st["arcs"]:
            if a["from"] not in at or a["to"] not in at:
                continue
            y1, y2 = y(a["from"]) + NODE_H // 2, y(a["to"]) + NODE_H // 2
            k = (a["from"], a["to"])
            lanes[k] = lanes.get(k, -1) + 1
            off = lanes[k] * 4
            x1 = PAD + depth[a["from"]] * INDENT + NODE_W
            x2 = PAD + depth[a["to"]] * INDENT + NODE_W
            bulge = min(36 + abs(y2 - y1) * 0.32, 300) + off * 3
            if y1 == y2:
                y2 += 6
            cls = "st-arc hand" if a["hand"] else "st-arc field"
            d = (f"M{x1},{y1 + off} H{rail} C{rail + bulge},{y1 + off} {rail + bulge},{y2} {rail},{y2} H{x2 + 4}")
            title = f'{rts[a["from"]].label} → {rts[a["to"]].label}: {a["label"]}'
            mid = (y1 + y2) / 2
            arcs.append(f'<g class="{cls}" data-from="{e(a["from"])}" data-to="{e(a["to"])}">'
                        f'<title>{e(title)}</title><path d="{d}" marker-end="url(#st-arrow)"/>'
                        + (f'<text x="{rail + bulge * 0.75 + 4:.0f}" y="{mid + 4:.0f}">{e(a["label"])}</text>'
                           if a["hand"] and (a["from"], a["rel"]) not in named else "") + '</g>')
            if a["hand"]:
                named.add((a["from"], a["rel"]))
        nodes = []
        for t, d in rows:
            rt = rts[t]
            x = PAD + d * INDENT
            badge = (f'<text class="st-badge" x="{x + NODE_W - 8}" y="{y(t) + 18}" text-anchor="end">'
                     f'{st["pending"][t]} proposed</text>' if t in st["pending"] else "")
            label = rt.plural if len(rt.plural) <= 24 else rt.plural[:23] + "…"
            nodes.append(
                f'<a href="?type={e(urllib.parse.quote(t))}#st-panel" class="st-node{" selected" if t == sel else ""}'
                f'{" proposed" if t in st["pending"] else ""}" data-type="{e(t)}" data-label="{e(rt.plural)}" '
                f'aria-label="{e(rt.plural)}{", part of " + e(rts[st["owner"][t]].plural) if st["owner"].get(t) else ""}">'
                f'<rect x="{x}" y="{y(t)}" width="{NODE_W}" height="{NODE_H}" rx="4"/>'
                f'<text x="{x + 10}" y="{y(t) + 18}">{e(label)}</text>{badge}</a>')
        width = rail + 340
        height = top + len(rows) * ROW + PAD
        root = (f'<g class="st-root" data-type=""><rect x="{PAD}" y="6" width="{NODE_W}" height="{NODE_H}" rx="4"/>'
                f'<text x="{PAD + 10}" y="24">Top level</text></g>')
        return (f'<svg class="st-svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="group" '
                f'aria-label="Record types: what is part of what, and their links">'
                '<defs><marker id="st-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" '
                'orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z"/></marker></defs>'
                f'{root}<g class="st-parts">{"".join(parts)}</g><g class="st-arcs">{"".join(arcs)}</g>'
                f'<g class="st-nodes">{"".join(nodes)}</g></svg>')

    def _structure_panels(self, req, st: dict, sel: str, kept: dict | None) -> str:
        """The selected type: what it is part of, its links, and the forms that propose a change to them."""
        rts = st["types"]
        if not sel:
            return ('<div id="st-panel"><p class="muted">Select a type to see what it is part of and the links it '
                    'allows, and to propose a change.</p></div>')
        rt = rts[sel]
        k = kept or {}
        out = [f'<div id="st-panel"><h2>{e(rt.plural)}</h2>'
               f'<p><a href="/define/types/{e(sel)}">The whole definition</a>'
               + (f' · <span class="loz moved">{st["pending"][sel]} proposed</span>' if sel in st["pending"] else "")
               + "</p>"]
        editable = sel in st["defined"]
        if not editable:
            out.append(f'<p class="muted">The practice has not defined {e(rt.plural.lower())} itself yet (its records '
                       f'are kept as they come). <a href="/define/types/new">Define it</a> to give it a structure.</p>')
        owner = st["owner"].get(sel)
        under = self._under(st, sel)
        opts = '<option value="">Nothing (top level)</option>' + "".join(
            f'<option value="{e(t)}"{" selected" if t == owner else ""}>{e(r.plural)}</option>'
            for t, r in rts.items() if t not in under)
        hidden = f'<input type="hidden" name="type" value="{e(sel)}">'
        note = ('<label>Why <input name="note" maxlength="500" placeholder="for the approver"></label>')
        if editable:
            out.append(f'<form method="post" action="/define/structure" class="card stack">{hidden}'
                       f'<input type="hidden" name="op" value="owner">'
                       f'<label>Part of <select name="owner">{opts}</select></label>'
                       f'<small class="muted">A part never exists without its owner and is shown on its owner\'s page.'
                       f'</small>{note}<button>Propose</button></form>')
        else:
            out.append(f'<p>Part of: {e(rts[owner].plural) if owner else "nothing (top level)"}</p>')
        out.append("<h3>Links made by hand</h3>")
        if rt.links:
            items = []
            for x in rt.links:
                to = ", ".join(rts[t].plural if t in rts else t for t in self.hub.concrete(x["to"]))
                drop = (f'<form method="post" action="/define/structure" class="inline">{hidden}'
                        f'<input type="hidden" name="op" value="drop-link"><input type="hidden" name="rel" '
                        f'value="{e(x["rel"])}"><button class="subtle small" aria-label="Remove the link '
                        f'{e(x["label"])}">Remove</button></form>' if editable else "")
                items.append(f'<li><b>{e(x["label"])}</b> <code class="muted">{e(x["rel"])}</code> → {e(to)}'
                             f'{"" if x["many"] else " <span class=muted>(one)</span>"}{drop}</li>')
            out.append(f'<ul class="plain st-links">{"".join(items)}</ul>')
        else:
            out.append('<p class="muted">None.</p>')
        refs = [f for f in rt.fields if f["type"] == "reference"]
        if refs:
            out.append('<details><summary>Links its fields make</summary><ul class="plain">' + "".join(
                f'<li>{e(f["label"])} → {e(", ".join(rts[t].plural if t in rts else t for t in self.hub.concrete(f.get("target") or [])))}'
                + (" <span class=muted>(its owner)</span>" if rt.part_of and f["name"] == rt.part_of["field"] else "")
                + "</li>" for f in refs) + "</ul></details>")
        if editable:
            targets = "".join(f'<option value="{e(t)}"{" selected" if t in (k.get("to") or []) else ""}>'
                              f'{e(r.plural)}</option>' for t, r in rts.items())
            out.append(f'<form method="post" action="/define/structure" class="card stack" id="st-add-link">{hidden}'
                       f'<input type="hidden" name="op" value="add-link"><h3>Add a link</h3>'
                       f'<label>Name <input name="label" required maxlength="80" value="{e(str(k.get("label") or ""))}" '
                       f'placeholder="e.g. Specialist\'s report"></label>'
                       f'<label>To <select name="to" multiple size="6" required>{targets}</select></label>'
                       f'<label class="check"><input type="checkbox" name="many" value="1"'
                       f'{"" if k and not k.get("many") else " checked"}> more than one may be linked</label>'
                       f'<label>Help <input name="help" maxlength="200" value="{e(str(k.get("help") or ""))}" '
                       f'placeholder="what to link, for the person linking"></label>{note}'
                       f'<button>Propose</button></form>')
        out.append("</div>")
        return "".join(out)

    def _structure_dialog(self, st: dict) -> str:
        """What a drop asks before it proposes: the move in words, and why."""
        return ('<dialog id="st-move" aria-labelledby="st-move-h"><form method="post" action="/define/structure" '
                'class="stack"><h2 id="st-move-h">Propose a change</h2><p id="st-move-what"></p>'
                '<input type="hidden" name="op" value="owner"><input type="hidden" name="type">'
                '<input type="hidden" name="owner"><label>Why <input name="note" maxlength="500" '
                'placeholder="for the approver"></label><div class="filters"><button class="primary">Propose</button>'
                '<button type="button" class="secondary" data-close>Cancel</button></div></form></dialog>')

    # ------------------------------------------------------------------------------------------ a change
    def post_structure(self, req):
        from ..hub import Invalid
        f = self.form(req)
        one = lambda k: (f.get(k)[0] if isinstance(f.get(k), list) else f.get(k)) or ""  # noqa: E731
        many = lambda k: [x for x in (f.get(k) if isinstance(f.get(k), list) else [f.get(k)]) if x]  # noqa: E731
        t, op, note = str(one("type")), str(one("op")), str(one("note")).strip()
        kept = {"type": t, "label": one("label"), "to": many("to"), "many": one("many"), "help": one("help")}
        try:
            source, words = self._structure_change(t, op, one, many)
            out = self.hub.propose("type", req["me"], source, note or words)
        except Invalid as exc:
            from .server import Rendered
            raise Rendered(422, self.page_structure({**req, "query": {}}, problems=exc.problems or [str(exc)],
                                                    kept=kept)) from None
        raise self._redirect(f"/define/structure?type={urllib.parse.quote(t)}"
                             f"&proposed={urllib.parse.quote(t)}:{out['version']}#st-panel")

    def _structure_change(self, t: str, op: str, one, many) -> tuple[str, str]:
        """The type's text in force with one change made, and the change in words (the proposal's note when the
        person gave none)."""
        from ..hub import Invalid
        hub = self.hub
        st = self._structure()
        rts = st["types"]
        if t not in st["defined"] or t not in rts:
            raise Invalid(f"{t or 'that'} is not a record type the practice has defined")
        rt = rts[t]
        versions = hub.definition_versions("type", t)
        src = schema.own_text(next((v for v in versions if v["status"] == "active"), versions[0])["source"])
        doc = tomllib.loads(src)
        if op == "add-link":
            label = str(one("label")).strip()
            rel = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
            if not label or not rel or not rel[0].isalpha():
                raise Invalid("name the link in words, starting with a letter")
            to = many("to")
            if not to:
                raise Invalid("say which kinds of record it links to")
            link = {"rel": rel, "label": label, "to": to}
            if not one("many"):
                link["many"] = False
            if str(one("help")).strip():
                link["help"] = str(one("help")).strip()
            # added as a table at the end: the rest of the text, its comments too, stays as it was
            extra = "\n[[link]]\n" + "".join(f"{_tk(k)} = {_tv(v)}\n" for k, v in link.items())
            return src.rstrip() + "\n" + extra, f"Add the link {label} to {rt.plural.lower()}"
        if op == "drop-link":
            rel = str(one("rel"))
            links = [x for x in doc.get("link", []) if x.get("rel") != rel]
            if len(links) == len(doc.get("link", [])):
                raise Invalid(f"{rt.plural.lower()} have no link {rel!r} to remove")
            if links:
                doc["link"] = links
            else:
                doc.pop("link", None)
            return toml_dumps(doc), f"Remove the link {rel} from {rt.plural.lower()}"
        if op == "owner":
            new = str(one("owner"))
            if new and new not in rts:
                raise Invalid(f"there is no record type {new}")
            if new and new in self._under(st, t):
                raise Invalid(f"{rts[new].plural} are part of {rt.plural.lower()}: a type is never part of its own "
                              f"part")
            if new == (st["owner"].get(t) or ""):
                raise Invalid(f"{rt.plural} are already {'part of ' + rts[new].plural.lower() if new else 'top level'}")
            fields = doc.setdefault("field", [])
            if not new:
                doc.pop("part_of", None)
                return toml_dumps(doc), f"{rt.plural} stand alone (no longer part of another record)"
            fd = next((x for x in fields if x.get("type") == "reference"
                       and new in hub.concrete([x["target"]] if isinstance(x.get("target"), str)
                                               else x.get("target") or [])), None)
            if fd is None:  # a reference to the owner, required: a part never exists without it
                name = new[:1].lower() + re.sub(r"\W", "_", new[1:])
                while any(x.get("name") == name for x in fields):
                    name += "_"
                fd = {"name": name, "label": rts[new].label, "type": "reference", "target": [new]}
                fields.append(fd)
            fd["required"] = True
            old_part = doc.get("part_of")
            doc["part_of"] = fd["name"]
            for sc in doc.get("screen", []):  # a required field is on every screen a record is made on
                if "create" not in (sc.get("on") or []):
                    continue
                lists = [sc["fields"]] if isinstance(sc.get("fields"), list) else \
                    [t["fields"] for t in sc.get("tab", [])[:1] if isinstance(t.get("fields"), list)]
                for names in lists:
                    if fd["name"] not in names:
                        at = names.index(old_part) + 1 if old_part in names else 0
                        names.insert(at, fd["name"])
            return toml_dumps(doc), f"{rt.plural} are part of {rts[new].plural.lower()}"
        raise Invalid(f"there is no change {op!r}")
