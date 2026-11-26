"""The graphical editor for views: the text it writes (checked like any view's, read back the same) and its pages,
which preview with the editor's own data and only ever propose."""

import json
import pathlib
import re

import pytest

from conftest import M
from practicehub import views as viewmod
from practicehub.hub import Denied
from practicehub.workflows import DefinitionError
from test_web import call, server, token  # noqa: F401 - server is a fixture

ROOT = pathlib.Path(__file__).resolve().parent.parent

DOC = {
    "id": "front-desk-day", "title": "Front desk day", "summary": "What the desk needs", "roles": ["front-desk"],
    "widget": [
        {"kind": "count", "title": "Planned visits", "type": "Encounter",
         "filter": {"match": "all", "rules": [{"path": "status", "op": "==", "value": "planned"}]},
         "grid": {"x": 0, "y": 0, "w": 3, "h": 2}},
        {"kind": "table", "title": "Visits", "type": "Encounter",
         "columns": [{"path": "status"}, {"path": "plannedStartDate", "label": "When"}],
         "sort": "plannedStartDate", "descending": True, "limit": 10,
         "filter": {"match": "any", "rules": [{"path": "status", "op": "in", "value": ["planned", "arrived"]},
                                              {"path": "plannedStartDate", "op": "<=", "today": "+7d"}]},
         "grid": {"x": 3, "y": 0, "w": 9, "h": 4}},
        {"kind": "chart", "title": "By status", "type": "Encounter", "group_by": "status",
         "grid": {"x": 0, "y": 2, "w": 3, "h": 3}},
        {"kind": "board", "title": "Work", "source": "items", "status": ["open"], "group_by": "role",
         "grid": {"x": 0, "y": 6, "w": 12, "h": 5}},
        {"kind": "list", "title": "Runs", "source": "runs", "columns": [{"path": "title"}],
         "grid": {"x": 0, "y": 11, "w": 6, "h": 3}},
        {"kind": "links", "title": "Go to", "links": [{"label": "Patients", "href": "/records/Patient"},
                                                      {"label": "Help", "href": "https://example.org/help"}],
         "grid": {"x": 6, "y": 11, "w": 6, "h": 3}},
    ],
}


def _same(a, b):
    return json.loads(json.dumps(a, sort_keys=True)) == json.loads(json.dumps(b, sort_keys=True))


# ---------------------------------------------------------------------------------------------- the text it writes
def test_the_editor_s_document_writes_a_view_that_reads_back_the_same():
    text = viewmod.to_toml(DOC)
    v = viewmod.parse(text)
    assert [w["kind"] for w in v.widgets] == ["count", "table", "chart", "board", "list", "links"]
    assert "record.status == 'planned'" in text and "today + 7d" in text and "grid = { x = 3" in text
    back = viewmod.doc_of(text)
    assert back["widget"][1]["filter"] == DOC["widget"][1]["filter"]  # the pickers come back as they were
    assert viewmod.to_toml(back) == text  # edit -> text -> edit -> the same text
    table = v.widgets[1]
    assert table["sort"] == "plannedStartDate" and table["descending"] and table["grid"]["w"] == 9


def test_every_view_a_module_ships_reads_back_the_same():
    files = sorted(ROOT.glob("modules/*/views/*.toml"))
    assert files
    for f in files:
        text = f.read_text()
        doc = viewmod.doc_of(text)
        again = viewmod.to_toml(doc)
        assert viewmod.parse(again).widgets == viewmod.parse(text).widgets, f.name
        assert _same(viewmod.doc_of(again), doc), f.name


def test_a_condition_the_pickers_cannot_show_stays_as_written():
    w = "record.status == 'planned' and (record.class == 'AMB' or record.class == 'VR')"
    assert viewmod.where_parts(w) is None
    doc = viewmod.doc_of(f'id = "x"\ntitle = "X"\n[[widget]]\nkind = "count"\ntype = "Encounter"\nwhere = "{w}"\n')
    assert "filter" not in doc["widget"][0] and viewmod.to_toml(doc).count(w) == 1


def test_the_editor_cannot_write_what_a_view_may_not_say():
    def problems(**w):
        try:
            viewmod.parse(viewmod.to_toml({"id": "x", "title": "X", "widget": [{"kind": "count", "type": "Patient", **w}]}))
        except DefinitionError as exc:
            return exc.problems
        return []
    assert any("12 columns" in p for p in problems(grid={"x": 10, "y": 0, "w": 4, "h": 2}))
    assert any("y is 0" in p for p in problems(grid={"x": 0, "y": 0, "w": 4, "h": 99}))
    bad = {"id": "x", "title": "X", "widget": [{"kind": "links", "links": [{"label": "x", "href": "javascript:alert(1)"}]}]}
    with pytest.raises(DefinitionError):
        viewmod.parse(viewmod.to_toml(bad))
    with pytest.raises(DefinitionError):  # a value is data: one with both kinds of quote cannot be written as one
        viewmod.where_of({"rules": [{"path": "status", "op": "==", "value": "it's \"x\""}]})
    with pytest.raises(DefinitionError):  # nor a path that is not one
        viewmod.where_of({"rules": [{"path": "status) or (1", "op": "==", "value": "x"}]})
    assert any("measure reads the fields of records" in p for p in problems(source="items", measure="sum(x)"))
    assert any("measure is sum(field)" in p for p in problems(measure="total"))
    assert any("x is a date or time by month" in p for p in problems(kind="line", x="birthDate"))
    assert any("series is for a line chart" in p for p in problems(kind="chart", group_by="gender", series="gender"))
    assert any("did you mean 'group_by'" in p for p in problems(kind="chart", group_by="gender", groupby="gender"))
    spec = viewmod.editor_spec()
    assert set(spec["kinds"]) == set(viewmod.WIDGETS)
    assert spec["measures"] == ["sum", "avg", "min", "max"] and spec["buckets"] == ["month", "quarter", "year"]


def test_a_view_s_numbers_read_back_the_same():
    doc = {"id": "numbers", "title": "Numbers", "widget": [
        {"kind": "count", "title": "Total", "type": "Order", "measure": "sum(total)",
         "where": "record.status != 'void'"},
        {"kind": "chart", "title": "By month", "type": "Order", "measure": "avg(total)", "group_by": "placed_on:month"},
        {"kind": "line", "title": "Over time", "type": "Order", "measure": "sum(lines.*.amount)",
         "x": "placed_on:quarter", "series": "customer.owner"},
        {"kind": "table", "title": "Sign-offs", "source": "signatures", "type": "Order",
         "columns": [{"path": "by"}, {"path": "current"}]},
        {"kind": "line", "title": "Signed", "source": "signatures", "x": "at:month"}]}
    text = viewmod.to_toml(doc)
    v = viewmod.parse(text)
    assert [w["kind"] for w in v.widgets] == ["count", "chart", "line", "table", "line"]
    assert v.widgets[2]["measure"] == "sum(lines.*.amount)" and v.widgets[2]["x"] == "placed_on:quarter"
    assert v.widgets[2]["series"] == "customer.owner" and v.widgets[4]["source"] == "signatures"
    assert 'x = "at:month"' in text and 'source = "signatures"' in text
    back = viewmod.doc_of(text)
    assert viewmod.to_toml(back) == text
    assert [w.get("measure") for w in back["widget"]] == ["sum(total)", "avg(total)", "sum(lines.*.amount)", None, None]


def test_the_hub_checks_the_fields_a_view_sorts_by(hub):
    text = viewmod.to_toml({"id": "x", "title": "X", "widget": [{"kind": "table", "type": "Encounter", "sort": "nope"}]})
    with pytest.raises(DefinitionError) as exc:
        hub.check_definition("view", text)
    assert any("no field 'nope'" in p for p in exc.value.problems)


# ---------------------------------------------------------------------------------------------- its pages
def test_the_editor_page_offers_every_kind_from_the_definitions(hub, server):
    rana = token(hub, "rana")
    code, page = call(server, "GET", "/define/views/new", rana)
    assert code == 200 and 'id="dash-editor"' in page and 'action="/define/views/design"' in page
    boot = json.loads(re.search(r'id="dz-boot">(.*?)</script>', page, re.S).group(1))
    assert set(boot["spec"]["kinds"]) == set(viewmod.WIDGETS) and boot["doc"]["widget"] == []
    assert any(t["id"] == "Encounter" for t in boot["catalog"]["types"]) and not boot["locked_id"]
    code, page = call(server, "GET", "/define/views/new?as=toml", rana)
    assert code == 200 and 'name="source"' in page and "dash-editor" not in page


def test_a_draft_is_written_checked_and_previewed_with_the_member_s_own_data(hub, server):
    code, out = call(server, "POST", "/api/views/draft", token(hub, "rana"), body={"doc": DOC})
    out = json.loads(out)
    assert code == 200 and out["problems"] == [] and out["source"] == viewmod.to_toml(DOC)
    assert len(out["widgets"]) == 6 and all("html" in w for w in out["widgets"])
    assert 'href="https://example.org/help"' in out["widgets"][5]["html"]
    # and back: the text to the document, every widget placed
    code, back = call(server, "POST", "/api/views/draft", token(hub, "rana"), body={"source": out["source"]})
    back = json.loads(back)
    assert back["doc"]["widget"][1]["filter"] == DOC["widget"][1]["filter"]
    assert all("grid" in w for w in back["doc"]["widget"])
    # what a member may not read is not previewed to them
    results = {"id": "r", "title": "R", "widget": [{"kind": "table", "type": "Observation", "columns": [{"path": "status"}]}]}
    seen = {}
    for who in ("rana", "dr-amara"):
        _, o = call(server, "POST", "/api/views/draft", token(hub, who), body={"doc": results})
        seen[who] = json.loads(o)["widgets"][0]
    assert seen["rana"]["note"] and "Not shown" in seen["rana"]["html"]
    assert not seen["dr-amara"]["note"]
    # and a draft with problems says so, widget by widget
    _, o = call(server, "POST", "/api/views/draft", token(hub, "rana"),
                body={"doc": {"id": "x", "title": "X", "widget": [{"kind": "table", "type": "NoSuchType"}]}})
    o = json.loads(o)
    assert o["problems"] and o["widgets"][0]["problems"]


def test_saving_from_the_canvas_proposes_and_never_activates(hub, server):
    rana, amara = token(hub, "rana"), token(hub, "dr-amara")
    source = viewmod.to_toml(DOC)
    code, page = call(server, "POST", "/define/views/design", rana, form={"source": source, "note": "for the desk"})
    assert code == 303
    assert hub.view("front-desk-day") is None  # proposed, not in force
    assert [v["status"] for v in hub.definition_versions("view", "front-desk-day")] == ["proposed"]
    with pytest.raises(Denied):  # whoever wrote it does not approve it
        hub.approve(M(hub, "rana"), "view", "front-desk-day", 1)
    hub.approve(M(hub, "dr-amara"), "view", "front-desk-day", 1)
    assert hub.view("front-desk-day") is not None
    # the view is laid out on its grid, and opens on the canvas again with its widgets placed
    code, page = call(server, "GET", "/views/front-desk-day", amara)
    assert code == 200 and 'class="dash-grid"' in page and "grid-column:4/span 9" in page
    code, page = call(server, "GET", "/define/views/front-desk-day/edit", rana)
    boot = json.loads(re.search(r'id="dz-boot">(.*?)</script>', page, re.S).group(1))
    assert boot["locked_id"] and len(boot["doc"]["widget"]) == 6
    # a change from the canvas is a new proposal; the version in force stays until it is approved
    changed = viewmod.to_toml({**DOC, "title": "Desk"})
    code, _ = call(server, "POST", "/define/views/design", rana,
                   form={"source": changed, "note": "shorter", "did": "front-desk-day"})
    assert code == 303 and hub.view("front-desk-day").title == "Front desk day"


def test_a_draft_that_is_not_a_view_is_refused_in_plain_words(hub, server):
    rana = token(hub, "rana")
    code, out = call(server, "POST", "/api/views/draft", rana, body={"source": "title = ["})
    assert code == 200 and "not valid TOML" in json.loads(out)["problems"][0]
    code, _ = call(server, "POST", "/api/views/draft", rana, body={"doc": "nope"})
    assert code in (400, 422)
    code, page = call(server, "POST", "/define/views/design-from-text", rana, form={"source": "title = [", "note": ""})
    assert code == 200 and "not valid TOML" in page and "dash-editor" not in page  # the text editor, with why


def test_an_item_widget_shows_what_a_step_wrote_only_to_who_may_read_it(hub, server):
    """The draft preview (and every dashboard) lists work items; their content follows the item page's rule."""
    from conftest import item_for
    patient = "Patient/" + next(r for r in hub.records.search() if r["resourceType"] == "Patient")["id"]
    run = hub.engine.start(M(hub, "jo"), "patient-question", record=patient, inputs={"question": "My knee hurts"})
    iid = item_for(hub, run, "clinician")
    hub.db.items.set(iid, {"status": "done", "output": {"answer": "SECRET-CLINICAL-NOTE"}})
    source = ('id = "x"\ntitle = "x"\n[[widget]]\nkind = "table"\nsource = "items"\nstatus = ["done"]\n'
              'columns = ["title", "output.answer"]\nlimit = 500\n')
    seen = {}
    for who in ("jo", "dr-amara"):
        _, o = call(server, "POST", "/api/views/draft", token(hub, who), body={"source": source})
        seen[who] = json.loads(o)["widgets"][0]
    assert "SECRET-CLINICAL-NOTE" not in json.dumps(seen["jo"]) and seen["jo"]["note"]
    assert "SECRET-CLINICAL-NOTE" in seen["dr-amara"]["html"]
    # nor is a filter on the content a way to learn it
    probe = source.replace('limit = 500', 'limit = 500\nwhere = "row.output.answer == \'SECRET-CLINICAL-NOTE\'"')
    _, o = call(server, "POST", "/api/views/draft", token(hub, "jo"), body={"source": probe})
    assert json.loads(o)["widgets"][0].get("total", 0) == 0


def test_the_editor_offers_the_fields_a_reference_reaches_and_a_table_s_columns(hub, server):
    from test_view_numbers import _patients, _sale, _types
    _types(hub)
    p1, _ = _patients(hub)
    _sale(hub, memo="s", patient=p1, amount="2.50", placed_on="2026-03-04")
    code, page = call(server, "GET", "/define/views/new", token(hub, "dr-amara"))
    boot = json.loads(re.search(r'id="dz-boot">(.*?)</script>', page, re.S).group(1))
    sale = next(t for t in boot["catalog"]["types"] if t["id"] == "Sale")
    assert {"name": "account.region", "label": "Account › Region", "type": "choice",
            "choices": ["east", "west"]} in sale["hops"]
    assert {"name": "lines.*.amount", "label": "Lines › Amount", "type": "decimal"} in sale["cells"]
    amount = next(f for f in sale["fields"] if f["name"] == "amount")
    assert amount["scale"] == 2 and amount["unit"] == "CAD"
    doc = {"id": "x", "title": "X", "widget": [{"kind": "line", "title": "Sold", "type": "Sale",
                                                 "measure": "sum(amount)", "x": "placed_on:month"}]}
    _, out = call(server, "POST", "/api/views/draft", token(hub, "dr-amara"), body={"doc": doc})
    out = json.loads(out)
    assert out["problems"] == [] and 'class="line-chart"' in out["widgets"][0]["html"]
    assert "2.50 CAD" in out["widgets"][0]["html"]
    _, out = call(server, "POST", "/api/views/draft", token(hub, "rana"), body={"doc": doc})
    w = json.loads(out)["widgets"][0]
    assert w["note"] == "needs access to clinical records" and "line-chart" not in w["html"]
