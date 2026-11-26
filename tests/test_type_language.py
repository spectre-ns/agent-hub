"""The record-type language: what ``schema.parse`` accepts, and the problems it names for what it does not."""

import glob
from pathlib import Path

import pytest

from conftest import M
from practicehub import schema
from practicehub.hub import Invalid
from practicehub.workflows import DefinitionError

ROOT = Path(__file__).parent.parent
HEAD = 'id = "Part"\ndata_class = "clinical"\n'
STATUS = ('[[field]]\nname = "status"\ntype = "choice"\nchoices = ["draft", "final"]\n'
          '[lifecycle]\nfield = "status"\ninitial = "draft"\n'
          '[[lifecycle.transition]]\nfrom = ["draft"]\nto = "final"\n')


def problems(src: str) -> list[str]:
    with pytest.raises(DefinitionError) as e:
        schema.parse(src)
    return e.value.problems


def ok(src: str) -> schema.RecordType:
    return schema.parse(src)


def test_every_shipped_type_still_parses():
    """The allow-lists are what parse reads: every type a pack, module or test pack ships is within them."""
    paths = [p for pat in ("packs/*/types", "packs/*/extends/types", "modules/*/types", "modules/*/extends/types",
                           "tests/packs/*/types") for p in glob.glob(str(ROOT / pat / "*.toml"))]
    assert paths
    unknown = []
    for p in paths:
        try:
            schema.parse(Path(p).read_text())
        except DefinitionError as exc:
            unknown += [f"{Path(p).name}: {x}" for x in exc.problems if "unknown key" in x]
    assert unknown == []


@pytest.mark.parametrize("src, want", [
    (HEAD + 'sumary = "x"\n', 'top level: unknown key "sumary" (did you mean "summary"?)'),
    (HEAD + 'lable = "x"\n', 'top level: unknown key "lable" (did you mean "label"?)'),
    (HEAD + '[[field]]\nname = "sku"\nrequried = true\n', 'field sku: unknown key "requried" (did you mean "required"?)'),
    (HEAD + '[[field]]\nname = "sku"\ntipe = "number"\n', 'field sku: unknown key "tipe" (did you mean "type"?)'),
    (HEAD + STATUS + 'bye = "workflow"\n', "lifecycle.transition to 'final': unknown key \"bye\" (did you mean \"by\"?)"),
    (HEAD + STATUS + '[[signature]]\nmeaning = "Approved"\nrole = ["x"]\n',
     "signature 'Approved': unknown key \"role\" (did you mean \"roles\"?)"),
    (HEAD + STATUS.replace('initial = "draft"\n', 'initial = "draft"\nlockd = ["final"]\n'),
     'lifecycle: unknown key "lockd" (did you mean "locked"?)'),
    (HEAD + STATUS + '[[screen]]\nid = "edit"\nfeilds = ["status"]\n',
     'screen edit: unknown key "feilds" (did you mean "fields"?)'),
    (HEAD + STATUS + '[[screen]]\nid = "edit"\n[[screen.tab]]\nlabel = "Main"\nfield = ["status"]\n',
     "screen edit tab 'Main': unknown key \"field\" (did you mean \"fields\"?)"),
    (HEAD + '[[field]]\nname = "up"\ntype = "reference"\ntarget = ["Part"]\nparent = "up"\n', None),
])
def test_unknown_keys_are_named_with_the_nearest_known_one(src, want):
    if want is None:  # ``parent`` inside the [[field]] table is a misplaced top-level key
        assert any('field up: unknown key "parent"' in p for p in problems(src))
    else:
        assert want in problems(src)


def test_navigator_unknown_keys_are_named():
    src = (HEAD + 'parent = "up"\n[[field]]\nname = "up"\ntype = "reference"\ntarget = ["Part"]\n'
           '[navigator]\ntop = "Spaces"\n')
    assert 'navigator: unknown key "top" (did you mean "tops"?)' in problems(src)


def test_a_lifecycle_says_where_a_new_record_starts():
    assert "lifecycle.initial: say which state a new record starts in" in problems(
        HEAD + STATUS.replace('initial = "draft"\n', ""))
    assert ok(HEAD + STATUS).lifecycle["initial"] == "draft"


def test_title_placeholders_date_and_flag_name_its_fields():
    base = HEAD + '[[field]]\nname = "sku"\n[[field]]\nname = "period"\ntype = "object"\n'
    assert "title: {skus} is not one of its fields (did you mean {sku}?)" in problems('title = "{skus}"\n' + base)
    assert any(p.startswith("date: 'when'") for p in problems('date = "when"\n' + base))
    assert any(p.startswith("flag: 'colour'") for p in problems('flag = "colour"\n' + base))
    rt = ok('title = "{sku}[ ({period})]"\ndate = "period.start"\nflag = "sku"\n' + base)
    assert rt.date == "period.start"
    # a base field counts: Subject's name is the record's name, whatever field holds it
    sub = ('id = "Client"\ndata_class = "clinical"\nextends = "Subject"\ntitle = "{name} {identifier}"\n'
           '[base]\nname = "full"\n[[field]]\nname = "full"\n')
    assert ok(sub).title


@pytest.mark.parametrize("line, want", [
    ('required = "yes"', "required is true or false"),
    ('advanced = 1', "advanced is true or false"),
    ('show = "no"', "show is true or false"),
    ('match = "x"', "match is true or false"),
    ('max_length = "long"', "max_length is a whole number"),
    ('type = "number"\nmin = "0"', "min is a number"),
    ('max = 3', "max is for a number or decimal field"),
    ('type = "choice"\nchoices = "a, b"', "choices is a list of words"),
    ('type = "choice"\nchoices = ["a", "a"]', "choices lists 'a' twice"),
    ('target = ["Part"]', "target is for a reference field"),
])
def test_field_options_are_type_checked(line, want):
    assert any(want in p for p in problems(HEAD + f'[[field]]\nname = "sku"\n{line}\n'))


@pytest.mark.parametrize("src", [
    HEAD + 'order = "ten"\n',
    HEAD + '[[field]]\nname = "status"\ntype = "choice"\nchoices = ["a"]\n[lifecycle]\nfield = "oops"\ninitial = "a"\n',
    HEAD + '[lifecycle]\nfield = 3\n',
    HEAD + 'lifecycle = "status"\n',
    HEAD + 'field = ["oops"]\n',
    HEAD + 'field = "oops"\n',
    HEAD + STATUS + '[[signature]]\nmeaning = "x"\nstates = 3\n',
    HEAD + 'extends = "Subject"\nbase = ["name"]\n',
    HEAD + STATUS + 'from = 3\n',
    HEAD + 'navigator = 3\n',
    HEAD + 'screen = [1]\n',
    HEAD + 'title = 3\n',
])
def test_bad_shapes_are_problems_not_crashes(src):
    assert problems(src)


def test_propose_reports_shape_errors_as_problems(hub):
    with pytest.raises(Invalid) as e:
        hub.propose("type", M(hub, "rana"), HEAD + 'order = "ten"\nfield = "oops"\n')
    assert any("order" in p for p in e.value.problems)


def test_a_workflow_only_field_is_not_required_nor_the_lifecycle_field():
    ps = problems(HEAD + '[[field]]\nname = "on_hand"\ntype = "number"\nby = "workflow"\nrequired = true\n')
    assert any("cannot be required" in p and "every new record would fail" in p for p in ps)
    ps = problems(HEAD + STATUS.replace('choices = ["draft", "final"]\n', 'choices = ["draft", "final"]\nby = "workflow"\n'))
    assert any(p.startswith("lifecycle.field: status says by") for p in ps)
    assert ok(HEAD + '[[field]]\nname = "on_hand"\ntype = "number"\nby = "workflow"\n')


def test_default_labels_split_camel_case():
    rt = ok(HEAD + '[[field]]\nname = "serviceProvider"\n[[field]]\nname = "lotNumber"\n'
                   '[[field]]\nname = "health_card"\n[[field]]\nname = "status"\n')
    assert [f["label"] for f in rt.fields] == ["Service provider", "Lot number", "Health card", "Status"]


def test_number_fields_have_a_min_and_max():
    rt = ok(HEAD + '[[field]]\nname = "quantity"\ntype = "number"\nmin = 0\nmax = 10.5\n')
    assert schema.validate(rt, {"quantity": -4}) == ["Part: Quantity is below 0"]
    assert schema.validate(rt, {"quantity": 11}) == ["Part: Quantity is above 10.5"]
    assert schema.validate(rt, {"quantity": 0}) == []
    assert any("min 5 is above max 1" in p for p in problems(
        HEAD + '[[field]]\nname = "q"\ntype = "number"\nmin = 5\nmax = 1\n'))


def test_two_fields_of_one_type_do_not_share_a_label():
    ps = problems(HEAD + '[[field]]\nname = "status"\n[[field]]\nname = "state"\nlabel = "status"\n')
    assert "field state: status has the same label 'status'; give one a label of its own" in ps


OWNED = ('id = "Allergy"\nlabel = "Allergy"\nplural = "Allergies"\ndata_class = "clinical"\n'
         '[[field]]\nname = "patient"\ntype = "reference"\ntarget = ["Patient"]\nrequired = true\n'
         '[[field]]\nname = "substance"\ntype = "string"\n')


def test_part_of_names_the_one_record_that_owns_it():
    rt = ok('part_of = "patient"\n' + OWNED)
    assert rt.part_of == {"field": "patient", "path": "patient", "target": ["Patient"]}
    assert ok(OWNED).part_of is None   # without it, a reference is a link: both records stand on their own


@pytest.mark.parametrize("src, want", [
    ('part_of = "substance"\n' + OWNED, "refers to one record"),
    ('part_of = "nope"\n' + OWNED, "refers to one record"),
    ('part_of = "patient"\n' + OWNED.replace("required = true\n", ""), "must be required"),
    ('part_of = "patient"\n' + OWNED.replace('target = ["Patient"]', 'target = ["Allergy"]'), "its own kind"),
])
def test_part_of_must_be_a_required_reference_to_another_type(src, want):
    assert any(want in p for p in problems(src))



def test_a_type_s_key_is_its_own_or_one_taken_from_its_label():
    """The key starts its records' keys (T1-12), as a Jira project's starts its issues'. One taken from a label always
    starts with a letter."""
    assert ok(HEAD + 'key = "T1"\n').key == "T1"
    assert ok(HEAD + 'label = "Engagement"\n').key == "ENG"
    assert ok(HEAD + 'label = "Change order"\n').key == "CO"
    assert ok(HEAD + 'label = "T1 return"\n').key == "T1R"
    assert ok(HEAD + 'label = "Slip or document"\n').key == "SD"
    assert schema.default_key("Résumé") == "RES" and schema.default_key("2024 file") == "F"
    for bad in ('"t1"', '"1T"', '"TOOLONGKEY"', '"T-1"', "1"):
        assert any(p.startswith("key: one to eight capital letters") for p in problems(HEAD + f"key = {bad}\n"))


def test_every_shipped_type_has_a_key_no_other_shipped_type_has():
    """Shipped types set their keys, unlike in any pack and module they may be installed beside: a module installs
    into any practice. (Two packs are never in one hub, so a pack's type may share a key with another pack's.)"""
    owners = {}
    for p in sorted(glob.glob(str(ROOT / "packs/*/types/*.toml")) + glob.glob(str(ROOT / "modules/*/types/*.toml"))):
        assert "\nkey = " in Path(p).read_text(), f"{p}: no key of its own"
        owners.setdefault(schema.parse(Path(p).read_text()).key, set()).add(Path(p).parts[-3])
    modules = {d.name for d in (ROOT / "modules").iterdir()}
    clash = {k: o for k, o in owners.items() if len(o) > 1 and o & modules}
    assert clash == {}


def test_two_types_with_one_key_are_refused(hub):
    with pytest.raises(Invalid) as e:
        hub.propose("type", M(hub, "rana"), 'id = "Pet"\nkey = "PAT"\ndata_class = "clinical"\n')
    assert any("already has the key 'PAT'" in p for p in e.value.problems)


NUMBERED = ('[[field]]\nname = "no"\ntype = "string"\n[[field]]\nname = "on"\ntype = "date"\n'
            '[[field]]\nname = "many"\ntype = "string"\nlist = true\n')


def numbered(table: str) -> str:
    return HEAD + "[number]\n" + table + NUMBERED + STATUS


def test_a_number_names_a_string_field_a_sound_pattern_its_date_and_states():
    """``[number]``: the field the hub writes it in (a string, one per record, not required), a pattern naming {seq}
    once and only letters, digits and - / . _ around it, the date a pattern with {year} or {month} reads, and states
    of its lifecycle. Its field leaves every form: the hub alone writes it."""
    rt = ok(numbered('field = "no"\npattern = "N-{year}{month}-{seq:05}"\ndate = "on"\nwhen = ["final"]\n'))
    assert rt.number == {"field": "no", "path": "no", "pattern": "N-{year}{month}-{seq:05}", "date": "on",
                         "when": ["final"]}
    assert rt.field("no")["by"] == "workflow"
    assert ok(numbered('field = "no"\npattern = "N{seq}"\n')).number["when"] == []  # given when the record is made
    assert ok(HEAD + 'date = "on"\n[number]\nfield = "no"\npattern = "{year}/{seq}"\n' + NUMBERED + STATUS
              ).number["date"] == "on"  # the type's own date by default
    for table, want in (
            ('pattern = "N{seq}"\n', "number.field: name the string field"),
            ('field = "many"\npattern = "N{seq}"\n', "number.field: many is a string field of several values"),
            ('field = "on"\npattern = "N{seq}"\n', "number.field: on is a date field"),
            ('field = "status"\npattern = "N{seq}"\n', "number.field: status is a choice field"),
            ('field = "no"\npattern = "N-{year}"\n', "number.pattern: 'N-{year}' names {seq}"),
            ('field = "no"\npattern = "{seq}{seq}"\n', "names {seq}"),
            ('field = "no"\npattern = "N {seq}"\n', "only letters, digits and - / . _"),
            ('field = "no"\npattern = "N{day}{seq}"\n', "only letters, digits"),
            ('field = "no"\npattern = "N-{year}-{seq}"\n', "number.date: 'N-{year}-{seq}' names the year or month"),
            ('field = "no"\npattern = "N{seq}"\ndate = "no"\n', "number.date: name its date field"),
            ('field = "no"\npattern = "N{seq}"\nwhen = ["sent"]\n', "number.when: 'sent' is not one of its states"),
            ('field = "no"\npattern = "N{seq}"\nwhen = "final"\n', "number.when: a list of states"),
            ('field = "no"\npatern = "N{seq}"\n', 'unknown key "patern" (did you mean "pattern"?)')):
        assert any(want in p for p in problems(numbered(table))), (table, problems(numbered(table)))
    req = numbered('field = "no"\npattern = "N{seq}"\n').replace('name = "no"\ntype = "string"\n',
                                                                 'name = "no"\ntype = "string"\nrequired = true\n')
    assert any("number.field: no is not required" in p for p in problems(req))


def test_a_number_is_counted_in_its_series():
    """Each year (or month) a pattern names is a series of its own, counted from 1; a number is read back as its
    place in its series."""
    from datetime import date
    d = date(2026, 3, 4)
    assert schema.number_series("TW-{year}-{seq:04}", d) == "TW-2026-{seq}"
    assert schema.render_number("TW-{year}-{seq:04}", d, 12) == "TW-2026-0012"
    assert schema.render_number("{year}{month}/{seq}", d, 12345) == "202603/12345"
    assert schema.number_in_series("TW-2026-{seq}", "TW-2026-0012") == 12
    assert schema.number_in_series("TW-2026-{seq}", "TW-2025-0012") is None
    assert schema.number_series("C-{seq:05}", None) == "C-{seq}" and schema.render_number("C-{seq:05}", None, 7) == "C-00007"


def test_final_states_pins_and_covers_are_declared_soundly():
    """``final`` names states (locked too); ``pin`` is for one reference field; ``covers`` names other types; a
    reversing field is the type's own and not required; a type reverses only what can be final."""
    rt = ok(HEAD + STATUS.replace('initial = "draft"\n', 'initial = "draft"\nfinal = ["final"]\n'))
    assert rt.is_final("final") and "final" in rt.lifecycle["locked"] and not rt.is_final("draft")
    assert any("lifecycle.final: 'done' is not one of its states" in p for p in
               problems(HEAD + STATUS.replace('initial = "draft"\n', 'initial = "draft"\nfinal = ["done"]\n')))
    pin = '[[field]]\nname = "ref"\ntype = "{t}"\ntarget = ["Part"]\npin = true\n'
    assert ok(HEAD + pin.format(t="reference")).fields[0]["pin"] is True
    assert any("pin" in p and "reference" in p for p in problems(HEAD + pin.format(t="string").replace('target = ["Part"]\n', '')))
    assert any("not a path through a list" in p for p in problems(
        HEAD + '[[field]]\nname = "ref"\npath = "rows.*.ref"\ntype = "reference"\ntarget = ["Part"]\npin = true\n'))
    for bad, want in (('covers = ["Part"]\n', "not part of itself"), ('covers = "Line"\n', "each once"),
                      ('covers = ["Line", "Line"]\n', "each once")):
        assert any(want in p for p in problems(HEAD + bad)), (bad, problems(HEAD + bad))
    rev = '[[field]]\nname = "of"\ntype = "reference"\ntarget = ["Part"]\n'
    final = STATUS.replace('initial = "draft"\n', 'initial = "draft"\nfinal = ["final"]\n')
    assert ok('reverses = "of"\n' + HEAD + rev + final).reverses["field"] == "of"
    assert any("required" in p for p in problems('reverses = "of"\n' + HEAD + rev + 'required = true\n' + final))
    assert any("a record is reversed once it is final" in p for p in problems('reverses = "of"\n' + HEAD + rev + STATUS))


def test_an_exact_number_is_declared_with_its_places_and_a_unit_only_beside_a_number():
    """A decimal says its places (scale, 0 to 10); its bounds are exact; a unit is a few characters beside a number."""
    from decimal import Decimal
    dec = '[[field]]\nname = "amount"\ntype = "decimal"\n'
    f = ok(HEAD + dec + 'scale = 2\nunit = "kg"\nmin = 0\n').fields[0]
    assert (f["scale"], f["unit"], f["min"]) == (2, "kg", Decimal("0"))
    places = "field amount: a decimal keeps a set number of places: scale = 2 (a whole number from 0 to 10)"
    for bad, want in ((dec, places), (dec + "scale = 11\n", places), (dec + "scale = 2.5\n", places),
                      ('[[field]]\nname = "n"\nscale = 2\n', "field n: scale is for a decimal field"),
                      ('[[field]]\nname = "n"\nunit = "kg"\n', "field n: unit is for a number or decimal field"),
                      ('[[field]]\nname = "n"\ntype = "number"\nunit = "' + "x" * 21 + '"\n',
                       'field n: unit is a few characters shown beside the value (at most 20), e.g. "%" or "kg"')):
        assert want in problems(HEAD + bad), (bad, problems(HEAD + bad))


def test_a_table_names_its_columns_each_one_value_a_row():
    """A table lists its columns ([[field.column]], each a field without a path, not a table); two columns never
    share a name; min_rows and max_rows (at most 1000, the default) are said of a table only."""
    tab = '[[field]]\nname = "lines"\ntype = "table"\n'
    col = '[[field.column]]\nname = "a"\n'
    rt = ok(HEAD + tab + col + '[[field.column]]\nname = "what"\nrequired = true\n')
    lines = rt.field("lines")
    assert [c["name"] for c in lines["columns"]] == ["a", "what"] and lines["max_rows"] == 1000
    assert lines["columns"][1]["label"] == "What" and [c["path"] for c in rt.cells()] == ["lines.*.a", "lines.*.what"]
    for bad, want in (
            (tab, "field lines: a table names its columns: [[field.column]] with name = ..., type = ..."),
            (tab + '[[field.column]]\nname = "sub"\ntype = "table"\n',
             "field lines, column sub: a column is one value in each row (string, text, number, decimal, boolean, "
             "date, datetime, choice, member, reference), not table"),
            (tab + col + 'path = "x"\n', "field lines, column a: path is said of the table, not of a column"),
            (tab + col + col, "field lines: two columns are called a"),
            (tab + "max_rows = 1001\n" + col, "field lines: max_rows is a whole number of rows, at most 1000"),
            (tab + "min_rows = 3\nmax_rows = 2\n" + col, "field lines: min_rows 3 is above max_rows 2"),
            ('[[field]]\nname = "lines"\nmin_rows = 1\n', "field lines: min_rows is for a table field"),
            (tab + col + 'type = "number"\nmin = "x"\n', "field lines, column a: min is a number, not 'x'"),
            (tab + col + 'labl = "A"\n', 'field lines, column a: unknown key "labl" (did you mean "label"?)')):
        assert want in problems(HEAD + bad), (bad, problems(HEAD + bad))


def test_a_types_checks_name_its_fields_and_a_moves_checks_its_parts():
    """A type's [[check]] says what must hold (must, over record.<field> and today) and, in words, what does not
    (message); a list is read through sum(), count(), min() or max(); a misspelt field, column or key is named with
    what was meant. Parts are read only by a move's check, as parts.<part type>.*, each its record and signed."""
    flds = ('[[field]]\nname = "amount"\ntype = "decimal"\nscale = 2\n[[field]]\nname = "lines"\ntype = "table"\n'
            '[[field.column]]\nname = "amount"\ntype = "decimal"\nscale = 2\n')
    good = '[[check]]\nmust = "record.amount == sum(record.lines.*.amount)"\nmessage = "the amount is the sum of its lines"\n'
    assert ok(HEAD + flds + good).checks == [{"when": None, "must": "record.amount == sum(record.lines.*.amount)",
                                              "message": "the amount is the sum of its lines", "parts": []}]
    chk = lambda must, more='message = "m"\n': f'[[check]]\nmust = "{must}"\n{more}'  # noqa: E731
    for bad, want in (
            (chk("record.amout > 0"), 'check 1: must: record.amout: there is no field \'amout\' (did you mean "amount"?)'),
            (chk("count(parts.Point.*) == 0"), "check 1: must: parts.Point.*: parts are read only by a move's check "
                                               "([[lifecycle.transition.check]]): a part is written apart from its record"),
            (chk("run.today > 0"), "check 1: must: run.today: a check reads record.<field> and today"),
            ('[[check]]\nmus = "record.amount > 0"\nmessage = "m"\n', 'check 1: unknown key "mus" (did you mean "must"?)'),
            ('[[check]]\nmus = "record.amount > 0"\nmessage = "m"\n',
             'check 1: must is the condition that holds, e.g. must = "record.end >= record.start"'),
            (chk("record.lines.*.amount > 0"), "check 1: must: record.lines.*.amount reads every item of a list: read "
                                               "it with sum(), count(), min() or max()"),
            (chk("sum(record.lines.*.amont) > 0"),
             "check 1: must: record.lines.*.amont: lines has no column 'amont' (did you mean \"amount\"?)"),
            (chk("record.amount > 2025-01-31"), "check 1: must: a date is written in quotes: '2025-01-31'"),
            (chk("record.amount > 0", ""),
             'check 1: message says, in words, what does not hold ("the total is the amount plus its lines")')):
        assert want in problems(HEAD + flds + bad), (bad, problems(HEAD + flds + bad))
    move = '[[lifecycle.transition.check]]\nmust = "{m}"\nmessage = "m"\n'
    rt = ok(HEAD + STATUS + move.format(m="count(parts.Point.*, it.record.status != 'closed') == 0"))
    assert rt.transition("draft", "final")["check"][0]["parts"] == ["Point"]
    for bad, want in (
            (move.format(m="count(parts.Point.*, it.recrd.status != 'closed') == 0"),
             "lifecycle.transition to 'final': check 1: must: parts.Point.*.recrd.status: a record's parts are read "
             "as parts.<part type>.*, each with its record.<field> and signed, e.g. count(parts.Point.*, "
             "it.record.status != 'closed')"),
            (move.format(m="record.stats == 'x'"),
             'lifecycle.transition to \'final\': check 1: must: record.stats: there is no field \'stats\' (did you mean '
             '"status"?)'),
            (move.format(m="count(parts.Point.*) == 0") + 'when = "x"\n',
             "lifecycle.transition to 'final': check 1: when: x: a check reads record.<field> and today, and "
             "parts.<part type>")):
        assert want in problems(HEAD + STATUS + bad), (bad, problems(HEAD + STATUS + bad))


def test_a_moves_stamp_is_one_member_date_or_time_field_the_move_alone_sets():
    """``stamp`` on a move names fields the move sets as it is made: each one member, date or datetime field, not the
    state, not a list and not one the move asks for; each is then off every form (``by = "workflow"``)."""
    flds = ('[[field]]\nname = "done_by"\ntype = "member"\n[[field]]\nname = "done_on"\ntype = "date"\n'
            '[[field]]\nname = "done_at"\ntype = "datetime"\n[[field]]\nname = "note"\n'
            '[[field]]\nname = "helpers"\ntype = "member"\nlist = true\n')
    rt = ok(HEAD + flds + STATUS + 'stamp = ["done_by", "done_on", "done_at"]\n')
    assert rt.transition("draft", "final")["stamp"] == ["done_by", "done_on", "done_at"]
    assert {rt.field(n)["by"] for n in ("done_by", "done_on", "done_at")} == {"workflow"}
    assert schema.stamped(rt) == {"done_by", "done_on", "done_at"}
    assert ok(HEAD + flds + STATUS + 'stamp = "done_by"\n').transition("draft", "final")["stamp"] == ["done_by"]
    where = "lifecycle.transition to 'final': "
    for bad, want in (('stamp = ["note"]', where + "stamp: note is string; a stamp is one member, date or datetime field"),
                      ('stamp = ["helpers"]', where + "stamp: helpers is a list; a stamp is one member, date or datetime "
                                                      "field"),
                      ('stamp = ["status"]', where + "the move itself sets status"),
                      ('stamp = ["nobody"]', where + "there is no field 'nobody'"),
                      ('stamp = ["done_by"]\nasks = ["done_by"]',
                       where + "stamp: done_by is asked for by the move as well; the move sets it, so it is not asked"),
                      ('stamp = [3]', where + 'stamp is a list of fields the move sets to who made it and when, e.g. '
                                              'stamp = ["reviewed_by", "reviewed_on"]')):
        assert want in problems(HEAD + flds + STATUS + bad + "\n"), (bad, problems(HEAD + flds + STATUS + bad + "\n"))


# ------------------------------------------------------------------ inheritance

PARENT = HEAD.replace('"Part"', '"Kit"') + '''abstract = true
extends = "Subject"
key = "KIT"
[[field]]
name = "code"
required = true
max_length = 12
[[field]]
name = "grade"
type = "choice"
choices = ["a", "b", "c"]
[[field]]
name = "weight"
type = "number"
min = 0
max = 100
[[field]]
name = "lead"
type = "reference"
target = ["Practitioner"]
roles = ["clinician", "nurse"]
[[field]]
name = "tags"
type = "string"
list = true
[[signature]]
meaning = "Checked"
roles = ["clinician"]
''' + STATUS.replace("draft", "open").replace("final", "shut")
UNDER = 'id = "Part"\nextends = "Kit"\n'


def under(src: str) -> schema.RecordType:
    return schema.parse(UNDER + src, parent=schema.parse(PARENT))


def under_problems(src: str) -> list[str]:
    with pytest.raises(DefinitionError) as e:
        under(src)
    return e.value.problems


def test_a_type_under_another_is_read_under_it():
    """Its parent's fields first, then its own; the parent's key, data class, lifecycle and signatures; its own id,
    label and plural; abstract only if it says so. The core base at the root is its base."""
    rt = under('[[field]]\nname = "colour"\n[[field]]\nname = "grade"\nchoices = ["a", "b"]\nrequired = true\n')
    names = [f["name"] for f in rt.fields]
    assert names == ["code", "grade", "weight", "lead", "tags", "status", "colour",
                     "name", "identifier", "contact", "responsible"]  # the parent's, its own, then the base's
    assert (rt.key, rt.data_class, rt.label, rt.plural, rt.abstract) == ("KIT", "clinical", "Part", "Parts", False)
    assert (rt.extends, rt.ancestors, rt.base) == ("Kit", ("Kit", "Subject"), "Subject")
    assert rt.field("grade")["choices"] == ["a", "b"] and rt.field("grade")["required"]
    assert rt.lifecycle["initial"] == "open" and [s["meaning"] for s in rt.signatures] == ["Checked"]
    assert under('key = "PT"\nabstract = true\n').key == "PT" and under('abstract = true\n').abstract


@pytest.mark.parametrize("src, want", [
    ('data_class = "demographics"\n', "data_class is Kit's: a type under it adds to it, never changes it"),
    ('[lifecycle]\nfield = "status"\ninitial = "shut"\n', "lifecycle is Kit's"),
    ('[[field]]\nname = "code"\ntype = "number"\n', "field code: its type is Kit's"),
    ('[[field]]\nname = "tags"\nlist = false\n', "field tags: its list is Kit's"),
    ('[[field]]\nname = "code"\npath = "c.0"\n', "field code: its path is Kit's"),
    ('[[field]]\nname = "code"\nrequired = false\n', "field code: Kit requires it, and so does every type under it"),
    ('[[field]]\nname = "code"\nmax_length = 20\n', "field code: max_length is 12 at Kit"),
    ('[[field]]\nname = "grade"\nchoices = ["a", "z"]\n', "a type under Kit gives fewer of its choices, never others ('z')"),
    ('[[field]]\nname = "weight"\nmin = -1\n', "field weight: min is 0 at Kit"),
    ('[[field]]\nname = "weight"\nmax = 101\n', "field weight: max is 100 at Kit"),
    ('[[field]]\nname = "lead"\nroles = ["clinician", "admin"]\n', "field lead: Kit lets members of clinician, nurse"),
    ('[[field]]\nname = "status"\nchoices = ["open"]\n', "its choices are the states of Kit's lifecycle"),
    ('[[field]]\nname = "name"\nlabel = "Kit name"\n', "field name: it is Subject's, as Kit has it"),
    ('[[signature]]\nmeaning = "Checked"\nroles = ["nurse"]\n', "signature 'Checked' is Kit's"),
])
def test_a_type_under_another_narrows_and_never_changes_it(src, want):
    assert any(want in p for p in under_problems(src)), under_problems(src)


def test_narrowing_a_field_keeps_what_it_does_not_say():
    """A field named again gives only what it changes: the rest is its parent's. Fewer choices keep their labels."""
    rt = under('[[field]]\nname = "code"\nlabel = "Part code"\nmax_length = 8\n'
               '[[field]]\nname = "weight"\nmin = 5\nmax = 50\n[[field]]\nname = "lead"\nroles = ["nurse"]\n')
    assert rt.field("code")["required"] and rt.field("code")["max_length"] == 8 and rt.field("code")["label"] == "Part code"
    assert (rt.field("weight")["min"], rt.field("weight")["max"]) == (5, 50) and rt.field("lead")["roles"] == ["nurse"]


def test_types_read_together_resolve_their_own_chains():
    """A pack's files, read without a hub: a type under another of them is read under it; a loop is refused."""
    leaf = 'id = "Leaf"\nextends = "Part"\n[[field]]\nname = "vein"\n'
    rts = schema.parse_all({"leaf": leaf, "part": UNDER, "kit": PARENT})
    assert rts["Leaf"].ancestors == ("Part", "Kit", "Subject") and rts["Leaf"].key == "KIT"
    assert rts["Leaf"].field("code")["required"] and rts["Leaf"].field("vein")
    with pytest.raises(DefinitionError, match="would be under itself"):
        schema.parse_all({"a": 'id = "A"\nextends = "B"\n', "b": 'id = "B"\nextends = "A"\ndata_class = "x"\n'})
    assert any("nor a record type in force here" in p for p in problems('id = "Lone"\nextends = "Nowhere"\n'))
