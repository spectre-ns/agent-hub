"""A view's numbers worked out in the database (``practicehub/views_query.py``): a measure, a date by month, one
reference followed, a line over time and the sign-offs, over the records the reader may read and no more."""

import json
from decimal import Decimal

import pytest

from conftest import M
from practicehub import expr
from practicehub import views as viewmod
from practicehub import views_query as vq
from practicehub.web import charts

ACCOUNT = '''id = "Account"
key = "ACX"
data_class = "billing"
[[field]]
name = "name"
[[field]]
name = "region"
type = "choice"
choices = ["east", "west"]
[[field]]
name = "holder"
type = "reference"
target = ["Patient"]
rel = "subject"
'''

SALE = '''id = "Sale"
key = "SLX"
data_class = "clinical"
[[field]]
name = "memo"
[[field]]
name = "patient"
type = "reference"
target = ["Patient"]
rel = "subject"
[[field]]
name = "account"
type = "reference"
target = ["Account"]
[[field]]
name = "amount"
type = "decimal"
scale = 2
unit = "CAD"
[[field]]
name = "qty"
type = "number"
[[field]]
name = "placed_on"
type = "date"
[[field]]
name = "status"
type = "choice"
choices = ["open", "won", "lost"]
[[field]]
name = "channel"
[[field]]
name = "flag"
type = "boolean"
[[field]]
name = "margin"
type = "decimal"
scale = 2
data_class = "billing"
[[field]]
name = "lines"
type = "table"
[[field.column]]
name = "what"
[[field.column]]
name = "amount"
type = "decimal"
scale = 2
'''


# ---------------------------------------------------------------------------------------------- helpers
def _type(hub, src, tid):
    hub.approve(M(hub, "dr-amara"), "type", tid, hub.propose("type", M(hub, "rana"), src)["version"])


def _types(hub):
    _type(hub, ACCOUNT, "Account")
    _type(hub, SALE, "Sale")


def _patients(hub, n=2):
    """Patients no directive names yet, so each test says what is withheld from whom."""
    named = {(c.get("patient") or {}).get("reference") for c in hub.records.search("Consent", limit=None)}
    out = [f"Patient/{p['id']}" for p in hub.records.search("Patient", limit=None)
           if f"Patient/{p['id']}" not in named]
    assert len(out) >= n
    return out[:n]


def _lockbox(hub, ref, **data):
    """Rana, the privacy officer, records the patient's directive."""
    return hub.create_record(M(hub, "rana"), "Consent", {
        "patient": {"reference": ref}, "status": "active", "received": "2026-11-01",
        "request": "Please keep these from everyone but my own doctor.", **data})


def _account(hub, holder, region, name="An account"):
    rec = hub.create_record(M(hub, "sam"), "Account", {"name": name, "region": region,
                                                       "holder": {"reference": holder}})
    return f"Account/{rec['id']}"


def _sale(hub, who="dr-amara", **data):
    """A sale, written by a physician (who writes clinical records; its margin is billing's to write)."""
    for k in ("patient", "account"):
        if isinstance(data.get(k), str):
            data[k] = {"reference": data[k]}
    rec = hub.create_record(M(hub, who), "Sale", data)
    return f"Sale/{rec['id']}"


def _widget(**kw):
    kw.setdefault("source", "records")
    if kw["source"] == "records":
        kw.setdefault("type", "Sale")
    return viewmod.widget(kw, 0)


def _problems(hub, **kw):
    one, wrong = _widget(**kw)
    return wrong or hub.check_view(viewmod.View(id="t", title="T", widgets=[one]))


def _show(hub, who, **kw):
    """One widget, checked as the hub checks a view, as the member sees it."""
    one, wrong = _widget(**kw)
    assert not wrong, wrong
    view = viewmod.View(id="t", title="T", widgets=[one])
    assert hub.check_view(view) == []
    return viewmod.compute(hub, M(hub, who), view)[0]


def _norm(groups):
    return {json.dumps(g["_id"], sort_keys=True, default=str): (g["n"], g["sum"], g["c"], g["lo"], g["hi"])
            for g in groups}


# ---------------------------------------------------------------------------------------------- the measure
def test_a_sum_is_worked_out_in_the_database_as_python_adds_it(hub):
    _types(hub)
    p1, p2 = _patients(hub)
    data = [("a", "won", "12.34", 2), ("b", "won", "0.66", 1), ("c", "lost", "5.00", 3), ("d", None, "1.25", None),
            ("e", "open", None, 4)]
    for memo, status, amount, qty in data:
        _sale(hub, memo=memo, patient=p1 if memo < "c" else p2,
              **({"status": status} if status else {}), **({"amount": amount} if amount else {}),
              **({"qty": qty} if qty is not None else {}))
    w = _show(hub, "dr-amara", kind="chart", group_by="status", measure="sum(amount)")
    assert w["computed"] and w["note"] is None
    assert w["counts"] == {"Open": Decimal("0.00"), "Won": Decimal("13.00"), "Lost": Decimal("5.00"),
                           "none": Decimal("1.25")}
    assert w["texts"]["Won"] == "13.00 CAD" and w["total"] == 5
    total = _show(hub, "dr-amara", kind="count", measure="sum(amount)")
    assert total["number"] == sum(Decimal(a) for _, _, a, _ in data if a) and total["stat"] == "19.25 CAD"
    assert _show(hub, "dr-amara", kind="count", measure="sum(qty)")["number"] == 10
    assert _show(hub, "dr-amara", kind="count", measure="max(placed_on)")["number"] is None
    # each record's projected row, grouped in Python, gives the database's groups
    one, _ = _widget(kind="chart", group_by="status", measure="sum(amount)")
    rt = hub.record_type("Sale")
    cols = vq.columns_of(hub.record_type, rt, one)
    today = hub.engine.now().date().isoformat()
    rows = list(hub.db.records.aggregate(vq.Pipeline(hub, M(hub, "dr-amara"), rt, today=today).build(
        cols, None, group=False)))
    assert len(rows) == 5 and all(set(r) == {"g", "s", "x", "m"} for r in rows)
    groups = list(hub.db.records.aggregate(vq.Pipeline(hub, M(hub, "dr-amara"), rt, today=today).build(cols, None)))
    assert _norm(vq.group_rows(rows)) == _norm(groups)


def test_a_computed_where_holds_exactly_where_the_hub_s_condition_does(hub):
    _types(hub)
    p1, _ = _patients(hub)
    rows = [dict(memo="m1", status="won", amount="0.10", qty=1, placed_on="2026-01-05", channel="web", flag=True),
            dict(memo="m2", status="lost", amount="5.00", qty=3, placed_on="2026-02-10", channel="phone", flag=False),
            dict(memo="m3", status="open", amount="7.50", qty=2, channel="web"),
            dict(memo="m4", amount="2.49", placed_on="2026-11-20", channel="shop"),
            dict(memo="m5", status="won", qty=9, placed_on="2025-12-31", flag=True),
            dict(memo="m6", status="won", amount="12.00", qty=12, placed_on="2026-11-24", channel="")]
    for r in rows:
        _sale(hub, patient=p1, **r)
    conds = ["record.status == 'won'", "record.status != 'won'", "record.status in ['won', 'lost']",
             "record.status not in ['won']", "record.status == null", "record.amount > 5", "record.amount >= 5.00",
             "record.amount < 2.5", "record.amount == 0.1", "record.qty > 2 and record.flag",
             "record.flag == false", "not record.flag", "record.flag", "record.channel",
             "record.placed_on >= '2026-02-01'", "record.placed_on < today - 30d",
             "record.channel == 'web' or record.qty == 3", "record.memo != ''", "'ph' in record.channel",
             "record.amount > record.qty", "record.qty <= 2 and not (record.status == 'open')",
             "record.channel in ['web', 'shop'] and record.placed_on != null"]
    amara = M(hub, "dr-amara")
    today = hub.engine.now().date().isoformat()
    listed = hub.search_records(amara, "Sale", limit=None)
    for c in conds:
        w = _show(hub, "dr-amara", kind="chart", group_by="memo", measure="sum(qty)", where=c)
        want = {r["memo"] for r in listed if expr.holds(c, {"record": r, "row": r, "today": today})}
        assert set(w["counts"]) == want, c


def test_a_decimal_total_is_exact_and_its_average_kept_to_its_places(hub):
    _types(hub)
    p1, _ = _patients(hub)
    for _ in range(3):
        _sale(hub, memo="dime", patient=p1, amount="0.10", qty=0.1)
    w = _show(hub, "dr-amara", kind="count", measure="sum(amount)")
    assert w["number"] == Decimal("0.30") and str(w["number"]) == "0.30" and w["stat"] == "0.30 CAD"
    _sale(hub, memo="lines", patient=p1, lines=[{"what": "a", "amount": "0.10"}] * 3)
    w = _show(hub, "dr-amara", kind="count", measure="sum(lines.*.amount)")
    assert str(w["number"]) == "0.30"
    _sale(hub, memo="more", patient=p1, amount="0.20")
    w = _show(hub, "dr-amara", kind="count", measure="avg(amount)")  # 0.50 over 4: 0.125, half up
    assert str(w["number"]) == "0.13"
    assert str(_show(hub, "dr-amara", kind="count", measure="min(amount)")["number"]) == "0.10"
    assert any("avg() over a table's column is not said" in p
               for p in _problems(hub, kind="count", measure="avg(lines.*.amount)"))
    assert any("reads a number or decimal field" in p for p in _problems(hub, kind="count", measure="sum(memo)"))


# ---------------------------------------------------------------------------------------------- who reads what
def test_a_number_the_reader_may_not_read_says_what_it_needs(hub):
    _types(hub)
    p1, _ = _patients(hub)
    acc = _account(hub, p1, "east")
    _sale(hub, memo="s", patient=p1, account=acc, amount="3.00")
    assert _show(hub, "rana", kind="count", measure="sum(amount)")["note"] == "needs access to clinical records"
    noor = _show(hub, "noor", kind="chart", measure="sum(amount)", group_by="account.region")
    assert noor["note"] == "needs access to billing records" and noor["counts"] == {}
    assert _show(hub, "noor", kind="count", measure="sum(margin)")["note"] == "needs access to billing records"
    assert _show(hub, "noor", kind="count", measure="sum(amount)",
                 where="record.margin > 1")["note"] == "needs access to billing records"
    assert _show(hub, "noor", kind="count", measure="sum(amount)")["number"] == Decimal("3.00")
    assert _show(hub, "dr-amara", kind="chart", measure="sum(amount)", group_by="account.region")["counts"] == {
        "East": Decimal("3.00")}


def test_what_a_subject_withheld_is_not_added_up(hub):
    _types(hub)
    p1, p2 = _patients(hub)
    _lockbox(hub, p1, withhold_classes=["clinical"], allow_members=[{"reference": "Practitioner/dr-amara"}])
    _sale(hub, memo="hers", patient=p1, amount="5.00", placed_on="2026-03-01")
    _sale(hub, memo="his", patient=p2, amount="7.00", placed_on="2026-03-02")
    assert _show(hub, "dr-amara", kind="count", measure="sum(amount)")["number"] == Decimal("12.00")
    for who in ("noor", "dr-liam"):
        assert _show(hub, who, kind="count", measure="sum(amount)")["number"] == Decimal("7.00"), who
        by_memo = _show(hub, who, kind="chart", measure="sum(amount)", group_by="memo")["counts"]
        assert by_memo == {"his": Decimal("7.00"), "none": Decimal("0.00")}, who  # still counted, none of it summed
        assert _show(hub, who, kind="count", measure="sum(amount)", where="record.memo == 'hers'")["number"] == 0


def test_a_reference_followed_shows_what_its_record_withholds_as_none(hub):
    _types(hub)
    p1, p2 = _patients(hub)
    _lockbox(hub, p1, withhold_classes=["billing"], allow_members=[{"reference": "Practitioner/dr-amara"}])
    east, west = _account(hub, p1, "east"), _account(hub, p2, "west")
    _sale(hub, memo="one", patient=p2, account=east, amount="1.00")
    _sale(hub, memo="two", patient=p2, account=west, amount="2.00")
    w = dict(kind="chart", measure="sum(amount)", group_by="account.region")
    assert _show(hub, "dr-amara", **w)["counts"] == {"East": Decimal("1.00"), "West": Decimal("2.00")}
    assert _show(hub, "dr-liam", **w)["counts"] == {"West": Decimal("2.00"), "none": Decimal("1.00")}
    # a chart counting by a reference followed is worked out too
    assert _show(hub, "dr-liam", kind="chart", group_by="account.region")["counts"] == {"West": 1, "none": 1}


# ---------------------------------------------------------------------------------------------- lines
def test_a_line_fills_the_months_between_and_says_what_it_did_not_draw(hub):
    _types(hub)
    p1, _ = _patients(hub)
    for on, amount in (("2026-01-15", "1.00"), ("2026-01-20", "2.00"), ("2026-03-05", "4.00"), (None, "8.00")):
        _sale(hub, memo="s", patient=p1, amount=amount, **({"placed_on": on} if on else {}))
    w = _show(hub, "dr-amara", kind="line", measure="sum(amount)", x="placed_on:month")
    line = w["line"]
    assert line["x"] == ["Jan 2026", "Feb 2026", "Mar 2026"] and line["unit"] == "CAD"
    assert [s["name"] for s in line["series"]] == ["Total of amount"]
    assert line["series"][0]["values"] == [Decimal("3.00"), Decimal("0.00"), Decimal("4.00")]
    assert line["series"][0]["texts"] == ["3.00 CAD", "0.00 CAD", "4.00 CAD"]
    assert w["aside"] == "1 with no placed on is not drawn"
    avg = _show(hub, "dr-amara", kind="line", measure="avg(amount)", x="placed_on:month")["line"]["series"][0]
    assert avg["values"] == [Decimal("1.50"), None, Decimal("4.00")] and avg["texts"][1] == "–"
    count = _show(hub, "dr-amara", kind="line", x="placed_on:quarter")["line"]
    assert count["x"] == ["Q1 2026"] and count["series"][0] == {"name": "How many", "values": [3], "texts": ["3"]}


def test_a_line_draws_eight_series_at_most_the_rest_as_other(hub):
    _types(hub)
    p1, _ = _patients(hub)
    for i in range(10):
        _sale(hub, memo=f"s{i}", patient=p1, channel=f"c{i}", amount=f"{i + 1}.00", placed_on="2026-01-10")
    series = _show(hub, "dr-amara", kind="line", measure="sum(amount)", x="placed_on:month",
                   series="channel")["line"]["series"]
    assert [s["name"] for s in series] == ["c9", "c8", "c7", "c6", "c5", "c4", "c3", "Other"]
    assert series[-1]["values"] == [Decimal("6.00")] and series[0]["values"] == [Decimal("10.00")]


def test_a_line_chart_is_drawn_with_its_numbers_in_a_table():
    one = {"x": ["Jan 2026", "Feb 2026", "Mar 2026"], "unit": "CAD", "measure": "Total of amount",
           "series": [{"name": "Total of amount", "values": [Decimal("3.00"), None, Decimal("4.00")],
                       "texts": ["3.00 CAD", "–", "4.00 CAD"]}]}
    out = charts.line_chart(one, "Billed")
    assert 'aria-label="Billed: Total of amount, Jan 2026 to Mar 2026"' in out
    assert out.count('class="vl-dot vl-s1"') == 2 and "vl-line" not in out  # a gap breaks the line: two dots
    assert "<details class='vl-data'>" in out and "<td class='num'>3.00 CAD</td>" in out
    assert "vl-legend" not in out and "<title>Feb 2026: –</title>" in out
    two = {**one, "series": [{"name": "East", "values": [1, 2, 3], "texts": ["1", "2", "3"]},
                             {"name": "West", "values": [3, 2, 1], "texts": ["3", "2", "1"]}]}
    out = charts.line_chart(two, "Billed")
    assert out.count('class="vl-line') == 2 and "<ul class='vl-legend'>" in out and "West" in out
    assert charts.line_chart({**one, "series": [{"name": "x", "values": [None] * 3, "texts": ["–"] * 3}]},
                             "Billed") == "<p class='muted'>Nothing to draw: no value in any of these.</p>"
    assert charts.line_chart({"x": [], "series": []}, "Billed") == "<p class='muted'>Nothing yet.</p>"


# ---------------------------------------------------------------------------------------------- sign-offs
def test_the_sign_offs_are_listed_counted_and_drawn(hub):
    _types(hub)
    p1, _ = _patients(hub)
    a, b = _sale(hub, memo="a", patient=p1), _sale(hub, memo="b", patient=p1)
    hub.sign_record(M(hub, "dr-amara"), a, "checked")
    hub.sign_record(M(hub, "dr-amara"), b, "checked")
    hub.sign_record(M(hub, "dr-liam"), b, "seen")
    table = _show(hub, "dr-amara", kind="table", source="signatures", type="Sale",
                  columns=["by", "meaning", "record", "current"])
    assert [(r["by"], r["meaning"], r["current"]) for r in table["rows"]] == [
        ("dr-liam", "seen", True), ("dr-amara", "checked", True), ("dr-amara", "checked", True)]
    hub.update_record(M(hub, "dr-amara"), b, {"memo": "b, changed"})
    table = _show(hub, "dr-amara", kind="table", source="signatures", type="Sale", columns=["by", "current"])
    assert {(r["record"], r["by"]): r["current"] for r in table["rows"]} == {
        (b, "dr-liam"): False, (b, "dr-amara"): False, (a, "dr-amara"): True}
    chart = _show(hub, "dr-amara", kind="chart", source="signatures", type="Sale", group_by="by")
    assert chart["computed"] and chart["counts"] == {"Dr. Amara Okafor": 2, "Dr. Liam Fraser": 1}
    line = _show(hub, "dr-amara", kind="line", source="signatures", type="Sale", x="at:month")["line"]
    assert len(line["x"]) == 1 and line["series"][0]["values"] == [3]
    assert any("measure reads the fields of records" in p
               for p in _problems(hub, kind="count", source="signatures", type="Sale", measure="sum(version)"))


# ---------------------------------------------------------------------------------------------- what a view may say
def test_a_view_s_numbers_say_only_what_can_be_worked_out(hub):
    _types(hub)
    assert _problems(hub, kind="line", x="placed_on:month", measur="sum(amount)") == [
        "widget 1: a widget has no key 'measur' (did you mean 'measure'?)"]
    assert any("a view follows one reference, not two" in p
               for p in _problems(hub, kind="chart", group_by="account.holder.name"))
    assert any("only a date or a time is grouped by month" in p
               for p in _problems(hub, kind="chart", group_by="status:month"))
    assert any("a board groups by a field of its own records" in p
               for p in _problems(hub, kind="board", group_by="account.region"))
    assert any("a board groups by a field as it is" in p
               for p in _problems(hub, kind="board", group_by="placed_on:month"))
    assert any("x is a date or time by month" in p for p in _problems(hub, kind="line", x="placed_on"))
    assert any("a line chart has no group_by" in p
               for p in _problems(hub, kind="line", x="placed_on:month", group_by="status"))
    assert any("series is for a line chart" in p
               for p in _problems(hub, kind="chart", group_by="status", series="channel"))
    assert any("measure is for a number, a bar chart or a line chart" in p
               for p in _problems(hub, kind="table", measure="sum(amount)"))
    assert any("holds more than one value" in p
               for p in _problems(hub, kind="chart", group_by="lines.*.what", measure="sum(amount)"))
    assert any("compares fields with values and today" in p
               for p in _problems(hub, kind="count", measure="sum(amount)", where="record.amount > record.qty + 1"))
    assert _problems(hub, kind="line", x="placed_on:year", series="account.region", measure="sum(lines.*.amount)") == []


@pytest.mark.parametrize("unit,keys,labels", [
    ("month", ["2025-11", "2026-02"], ["Nov 2025", "Dec 2025", "Jan 2026", "Feb 2026"]),
    ("quarter", ["2025-Q4", "2026-Q2"], ["Q4 2025", "Q1 2026", "Q2 2026"]),
    ("year", ["2024", "2026"], ["2024", "2025", "2026"])])
def test_buckets_run_from_the_first_to_the_last_with_none_left_out(unit, keys, labels):
    assert [vq.bucket_label(k, unit) for k in vq.fill(keys, unit)] == labels
