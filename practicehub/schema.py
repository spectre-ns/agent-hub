"""Record types: the object model, as data.

A record type is a TOML document, versioned and approved like a workflow (change control ``kind = "type"``). A pack
or module ships the first versions in ``types/*.toml``; a practice changes them from the UI (``/types``). Every
record the hub writes is checked against its type, whoever writes it: a person's form, an automation, an agent's
answer that becomes a record, an engine calling the API.

    id = "Observation"            # the record type (FHIR resource names, or a module's own: Document, CAPA, Part)
    label = "Result"
    plural = "Results"
    key = "LAB"                   # its records' keys, counted per type: LAB-1, LAB-2... (one to eight capital letters
                                  #   and digits, starting with a letter). Given when a record is made and never
                                  #   changed; work on the record shows it. None set: taken from the label (Result:
                                  #   RES). Once records carry it, the type keeps it, and no other type may take it.
    [number]                      # optional: a number of its own the hub gives each record once, in a series with no
    field = "number"              #   gaps (a document number). A string field, not required: the hub writes it, and
                                  #   nobody else (no person, workflow or import) does
    pattern = "TW-{year}-{seq:04}"  # {seq} once ({seq:04}: at least four digits); {year} and {month} of its date; between
                                  #   them letters, digits and - / . _. Each year (or month) the pattern names counts
                                  #   from 1 again
    date = "issued_on"            #   the date field it reads (default: the type's date); a record numbered by its date
                                  #   keeps that date
    when = ["sent"]               #   given by the write that puts the record in one of these states (default: when it
                                  #   is made). A numbered record keeps its number and is never converted into another
                                  #   type: no number is skipped or given twice
    reverses = "reversal_of"      # a reference field to this same type: a record corrects one in a final state (below)
                                  #   by reversing it. It reverses one that is final, that nothing else reverses, and
                                  #   which one never changes. The core copies the record's values into the new one's
                                  #   form; what changes in it (a sign, say) is the pack's to say
    covers = ["Line"]             # part types (each ``part_of`` this one) its signatures and locks also cover: a part
                                  #   changed leaves a signature on this record not current; while this record is
                                  #   locked people do not add or change its parts (its workflow still does), and while
                                  #   it is final no one does
    [period]                      # optional: its records are dated in periods a record of another type closes
    closed_by = "PeriodClose"     #   that type (it has [closing]). Once a period is closed, nothing dated on or before
                                  #   its last day is written, moved into it or out of it, by anyone
    date = "posted_on"            #   a required date (or datetime) field (default: the type's date)
    scope = "book"                #   optional: a reference field; a close then reaches only records with its own
                                  #   (the close's scope names the same type). None: one close covers every record
    [closing]                     # optional: its records close a period of the types that name it in [period]
    through = "through"           #   a required date field: the last day it closes
    states = ["closed"]           #   lifecycle states it closes in: locked, nothing changes it, and only a signed move
                                  #   (``sign`` on every transition out of them) reopens it
    scope = "book"                #   with [period] scope: the reference field naming what it closes
    data_class = "clinical"       # who may read and write it (grants are per data class)
    subject = false               # true: work is about records of this type (Patient, Client, Matter)
    strict = true                 # refuse top-level keys no field declares
    date = "effectiveDateTime"    # the path that dates a record (charts and lists sort by it, newest first)
    flag = "interpretation.0.text"  # a path whose value is shown as a flag (abnormal, high, overdue...)
    title = "{name}[ (born {birthDate})]"   # how a record is named: field (or base field) values; [...] is optional
    short_title = "{name}"        # how it is named where a name travels on screen (tab title, breadcrumbs, headings,
                                  #   tooltips, notices): leave out an identifier that belongs only on the record itself
    parent = "parent"             # a reference field to the same type: its records form a tree (pages under pages, folders)
    part_of = "subject"           # a reference field (or base field) naming the one record that owns it: an allergy or
                                  #   a visit is part of its patient, a visit note part of its visit. It is reached only
                                  #   through its owner (no list or create of its own). A record that merely refers to
                                  #   another (a result to the order it answers) links to it instead: ``rel`` on the
                                  #   reference field.
    [navigator]                   # with a tree: a second navigation column, its tops (spaces) and the open one's tree
    tops = "Spaces"               #   what the records at the top of the tree are called
    hide = ["archived"]           #   lifecycle states left out of it (a record in one still shows when it is open)
    standard = "fhir-r4"          # its records are resources of a standard (FHIR R4): exported as such (practicehub/export.py)
    [consent]                     # a commercial message (Canada's CASL) goes to a record of it only while its recorded
    kind = "consent"              #   consent holds (``Hub.consent_problem``): a choice field, the kind of consent;
    date = "consent_date"         #   the date field it was given on (none, or later than today: it does not hold);
    source = "consent_source"     #   where it came from (empty: it was never recorded, so it does not hold);
    withdrawn = "consent_withdrawn"   # a date field: withdrawn on or before today, it no longer holds;
    lasts = { express = "", implied = "2y" }   # the kinds that count, and how long each lasts ("": until withdrawn)
    layout = "page"               # "record" (the default) or "page": its formatted text is the page, read first, with
                                  #   the other fields and the controls in a bar above it, and edited in place
    alert = false                 # show active records of this type in a banner on the subject's page
    active = "record.status != 'inactive'"   # which records are current (a condition over ``record``)
    order = 10                    # where it sits on a subject's page

    [[field]]
    name = "status"               # the key in the record, unless ``path`` says where (``code.text``, ``name.0.family``)
    type = "choice"               # string text markdown number decimal boolean date datetime choice member reference
                                  #   attachment json object list table
    choices = ["preliminary", "final", "amended"]
    # labels = { amended = "Corrected" }   # a choice's words where its code is not words (AMB = "In clinic")
    required = true

    [[field]]
    name = "subject"
    type = "reference"            # {"reference": "Patient/<id>"} or "Patient/<id>"
    target = ["Patient"]
    rel = "subject"               # the link written for it; ``subject`` makes it what the record is about
    # pin = true                  # keeps the version and hash of what it refers to, as they were when it was given:
                                  #   {"reference", "version", "sha256"}. A signature on this record covers what it
                                  #   pins; it is not signed while a pin is behind, and only "Use version M" (repin)
                                  #   moves one on. One field (``list = true`` for several), not a path through a list
    # fact = true                 # shown among a subject's headline facts
    # match = true                # with the name, how a second registration of the same subject is noticed
    # data_class = "clinical"     # a field of its own data class: only who may read that class sees it (on every
                                  #   page, list, export and history), only who may write it sets it. The type's class
                                  #   still says who sees the record exists, lists it and creates it.

    [[field]]
    name = "file"
    type = "attachment"           # one stored file: {"blob": <sha256>, "name", "contentType", "size"}
    [[field]]
    name = "text"
    type = "text"
    from_file = "file"            # filled with the words of the file in ``file`` when one is attached (a document,
                                  #   a text file, a PDF's text layer), unless the same write gives it. A picture or
                                  #   a scan has no words here: the field is left as it was, for an agent that can see
                                  #   (a step's ``file:<field>`` context) or a person

    [[field]]
    name = "amount"
    type = "decimal"              # an exact number (money, a quantity, a rate): kept to ``scale`` places, rounded half
    scale = 2                     #   up as it is written (12.345 is 12.35), never a float: 0.10 + 0.20 is 0.30. At most
    # unit = "%"                  #   15 digits. ``unit``: a few characters shown beside it (also on a number). min and
    min = 0                       #   max are exact too. JSON gives it as its text ("12.30"); FHIR as a number

    [[field]]
    name = "lines"
    type = "table"                # rows of the same columns (the lines of an order, a log): each row checked as its
    min_rows = 1                  #   columns say ("Lines, row 2: Amount is required"); at most max_rows (1000)
    [[field.column]]              # a column is a field of one value: string text number decimal boolean date datetime
    name = "item"                 #   choice member reference. It has no path of its own (it is at lines.*.item: a
    type = "reference"            #   reference column links as a reference field does); a table's ``by``, data class
    target = ["Part"]             #   and path are said of the table
    [[field.column]]
    name = "amount"
    type = "decimal"
    scale = 2
    required = true

    [[check]]                     # a rule every write keeps, whoever writes (a person, the API, an agent, a workflow,
    when = "record.status != 'draft'"   # a convert; a pack upgrade carries records over as they were): what ``must``
    must = "record.amount == sum(record.lines.*.amount)"   # hold while ``when`` does (none: always), over the record
    message = "the amount is the sum of its lines"         # by its fields' names and ``today``; ``message`` says why
                                  #   not. A record's check reads only the record (parts: a move's check, below)

    [lifecycle]                   # optional: the states a record moves through
    field = "status"              # the field that holds the state; its choices are the states
    initial = "preliminary"
    # locked = ["final"]          # people change nothing but the state (its own workflow still writes it)
    # final = ["entered"]         # nothing changes it, no one (a workflow, an upgrade, a signature's move), and no move
                                  #   leaves it (``from = ["*"]`` does not match it); it is corrected by ``reverses``
    # abandoned = { amended = "final" }   # where a record goes when the work moving it is cancelled: a move of its
                                  #   own, with no transition written for it; a state named as its own target stays
    [[lifecycle.transition]]      # a move people make by hand
    from = ["final"]
    to = "amended"
    # label = "Amend"             # the move's words (default: the state it goes to, in words)
    # asks = ["conclusion"]       # fields the move's screen asks for, written in the same move
    # requires = ["conclusion"]   # fields that must be set once it has moved (whoever moves it, a workflow too);
                                  #   the type's own field rules check what they hold
    # sign = "Approved"           # the mover signs it with this meaning (a person: an agent never signs)
    # stamp = ["amended_by", "amended_on"]   # fields the move sets as it is made, in its own write (a signature
                                  #   that makes it covers them): a member field to who made it (for a workflow's
                                  #   move, whoever did the step), a date to today, a time to now. Nothing else
                                  #   writes them (an import that brings a record in as it was aside); none is
                                  #   asked for, a list or the state field
    # by = "workflow"             # not by hand: see below
    # [[lifecycle.transition.check]]   # the move is made only while this holds, whoever makes it (a person, a
    # must = "count(parts.Point.*, it.record.status != 'closed') == 0"   # workflow, a signature): a condition over
    # message = "every review point is closed"   # the record as it would be once moved, ``today``, and its parts:
                                  #   ``parts.<part type>`` is a list of {record, signed}, ``signed`` the meanings of
                                  #   the part's current signatures (``'Reviewed' in it.signed``). Its parts are read for
                                  #   the hub, whatever the mover may see; the refusal says only the message. A task
                                  #   whose move it refuses is not done
    # [lifecycle.labels]          # a state's words, where its code is not words: onleave = "On leave"

A type that names its own workflow (``workflow``) lists the values its state field takes and the moves people make
by hand. The moves that workflow makes are its edges (``practicehub/states.py``), not written here: read with its
workflow in force, the type has them as moves by the workflow (``CatalogMixin._with_workflow_moves``), and the
workflow's states carry their own words and tone. What the two may not both say is checked by
``catalog.lifecycle_fit``. A transition ``by = "workflow"`` is still the type's to write for a move no edge of its
own workflow makes (one made from outside its own run: another workflow's, or a signature's ``moves_to``), for a move
from a value its workflow has no state for, and for a move it attaches ``asks``, ``requires``, ``sign``, ``stamp`` or
a ``check`` to: an edge making that move keeps what the row says.

A path may use ``*`` for every item of a list (``participant.*.actor``).

Inheritance. A type may extend another (``extends = "<Type>"``): one of the core's base types, or any type of the
hub. The core ships abstract base types (``practicehub/base/*.toml``: ``Subject``, ``Booking``) that say what the core
and its modules need from a record, and nothing about any practice. A type that extends one says which of its fields
holds each of the base's fields:

    id = "Appointment"
    extends = "Booking"           # the core's calendar, free slots and booking.book now handle it
    [base]                        # which of its fields holds each of the base type's fields
    reason = "description"        # its own field (same name: nothing to say; absent: the base field is inherited)
    subject = "patient"           # a reference to a type that extends the base's target (Patient extends Subject)
    with = "participants"         # references to member types give the members' ids
    cancelled = { when = "record.status in ['cancelled', 'noshow']" }   # a condition, for a true/false field
    # name = ["given", "family"]  # several fields: their words, in order (read only)
    # contact = false             # an optional base field it does not keep

The core reads such a record only through ``project`` (the base fields, as the base declares them) and writes one
through ``compose``. That is the contract between core and pack (``contract.py`` exports it as Avro schemas).

A type may extend any of the hub's own types as well: a module's, the practice's or an overlay's. A chain runs from a
type that extends nothing or a core base, through at most eight types, and never loops; a module's types extend only
the core's, the module's own and those of the modules it requires. A type under another is its parent and more: its
definition is its parent's as in force, with what it says itself merged in.

    id = "Party"                  # abstract: no records of its own; the types under it have them
    abstract = true
    extends = "Subject"
    data_class = "parties"
    key = "PTY"                   # shared: a person's and an organization's keys are PTY-1, PTY-2, ...
    [[field]]
    name = "name"
    [[field]]
    name = "email"

    id = "Person"
    extends = "Party"
    [[field]]                     # a field of its own
    name = "birth_date"
    type = "date"
    [[field]]                     # one of its parent's, narrowed: it says only what it changes
    name = "email"
    required = true

- Fixed, the parent's: data_class, [base], lifecycle, workflow, [number], reverses, covers, [period], [closing],
  part_of, parent, consent, booking, standard, strict, subject, active, date and flag. A type under it that says one
  otherwise is refused.
- Added to: its fields, checks, signatures (each a meaning of its own), links (each a rel of its own) and unique keys
  come after its parent's.
- Narrowed: a field named as one of its parent's says only what it changes. It may make it required, give fewer
  choices (not of the lifecycle's field), a tighter min, max, max_length, min_rows or max_rows, fewer roles, a target
  that is under the parent's, and its own label and help. Its type, path, list, data class, by, pin, rel, with,
  live, scale, unit, required_unless and columns stay.
- Its own when it says one, else its parent's: summary, title, short_title, order, layout, navigator, alert, its
  screens (one of the same id replaces the parent's; a parent's screen it keeps gets a last tab, its label, of the
  fields it adds) and key. A type that sets no key shares its parent's, and so
  the counter its records' keys are given from.
- Never inherited: id, label, plural and slug (each from its own id when it says none), and abstract.

``abstract = true``: nothing creates a record of the type; the types under it have the records (those it had before
it became abstract stay, and change only by moving under it). Naming a type (a reference's target, a view, a data
class, a workflow's record) takes in every type under it, its family. A family shares its key and counter, its
number's series (``[number]`` is the top type's), each unique key across the family of the type that set it, and
its ids; a record moved to another type of its family keeps its key and number.
"""

from __future__ import annotations

import difflib
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import decimals, expr
from .workflows import DefinitionError

TYPE_ID = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")
# A type's key: the start of its records' keys (T1-12, ENG-3), as a Jira project's key starts its issues'.
TYPE_KEY = re.compile(r"[A-Z][A-Z0-9]{0,7}")
_KEY_SMALL_WORDS = {"a", "an", "and", "of", "or", "the", "to", "for", "in", "on"}


def default_key(label: str) -> str:
    """The key of a type that does not set one, from its label: a word's first three letters (Engagement: ENG), or the
    first letter of each word (Change order: CO), a word with digits in it whole (T1 return: T1R)."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", label or "") if not unicodedata.combining(c))
    words = [w for w in re.split(r"[^A-Za-z0-9]+", plain) if w and w.lower() not in _KEY_SMALL_WORDS]
    if len(words) == 1:
        key = words[0][:3].upper()
    else:
        key = "".join(w.upper() if any(c.isdigit() for c in w) else w[0].upper() for w in words[:4])[:8]
    key = key.lstrip("0123456789")  # a key starts with a letter (2024 file: F)
    return key or "REC"


# A ``[number]`` pattern: {seq} once, {year} and {month} of the record's date, and between them these characters only.
NUMBER_TOKEN = re.compile(r"\{(year|month|seq(?::0([1-9]))?)\}")
NUMBER_TEXT = re.compile(r"[A-Za-z0-9\-/._]*")


def number_reads_date(pattern: str) -> bool:
    """Whether a ``[number]`` pattern names its record's date ({year} or {month})."""
    return any(m.group(1) in ("year", "month") for m in NUMBER_TOKEN.finditer(pattern))


def number_series(pattern: str, d: date | None) -> str:
    """The series a number of this pattern dated ``d`` is counted in: the pattern with its year and month filled in
    ("TW-{year}-{seq:04}" on 2026-03-04: "TW-2026-{seq}"). Each series counts from 1."""
    def fill(m):
        return {"year": f"{d.year:04d}" if d else "", "month": f"{d.month:02d}" if d else ""}.get(m.group(1), "{seq}")
    return NUMBER_TOKEN.sub(fill, pattern)


def render_number(pattern: str, d: date | None, seq: int) -> str:
    """The ``seq``-th number of its series ("TW-{year}-{seq:04}", 2026-03-04, 12: "TW-2026-0012")."""
    def fill(m):
        if m.group(1) == "year":
            return f"{d.year:04d}"
        if m.group(1) == "month":
            return f"{d.month:02d}"
        return str(seq).zfill(int(m.group(2) or 1))
    return NUMBER_TOKEN.sub(fill, pattern)


def number_in_series(series: str, number) -> int | None:
    """The place of ``number`` in ``series`` (``number_series``), or None when it is not one of the series'."""
    if not isinstance(number, str):
        return None
    head, _, tail = series.partition("{seq}")
    m = re.fullmatch(re.escape(head) + r"([0-9]{1,18})" + re.escape(tail), number)
    return int(m.group(1)) if m else None


FIELD_TYPES = {"string", "text", "markdown", "number", "decimal", "boolean", "date", "datetime", "choice", "member",
               "reference", "attachment", "json", "object", "list", "table"}
# what a table's column may be: one plain value in each row
COLUMN_TYPES = ("string", "text", "number", "decimal", "boolean", "date", "datetime", "choice", "member", "reference")
MAX_ROWS = 1000
RESERVED = {"resourceType", "id", "meta"}
BY = ("anyone", "workflow")
OPS = ("create", "edit", "view")   # what a screen is shown for (a transition names its own screen)
SCREEN_ID = re.compile(r"[a-z][a-z0-9-]*")
BASE_DIR = Path(__file__).parent / "base"
ROOT = "Record"  # every record type is a Record
FIELD_PATH = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)*")  # keys only: what a store can range over


class SchemaError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class RecordType:
    id: str
    label: str
    plural: str
    data_class: str
    fields: list[dict] = field(default_factory=list)
    subject: bool = False
    strict: bool = True
    date: str | None = None
    flag: str | None = None
    alert: bool = False
    active: str | None = None
    order: int = 100
    lifecycle: dict | None = None
    summary: str = ""
    source: str = ""
    signatures: list[dict] = field(default_factory=list)  # [{meaning, roles, states}]: who signs it, and for what
    abstract: bool = False   # nothing creates a record of it; the types under it have them (a core base type, say)
    extends: str | None = None   # the type it extends: its parent, a core base type or one of the hub's
    base_map: dict = field(default_factory=dict)   # base field -> {"fields": [own field names]} or {"when": expr}
    title: str | None = None  # how a record of it is named: "{name}", "{start}[ {reason}]"
    short_title: str | None = None  # how it is named in navigation (tab title, crumbs, headings, tooltips); else title
    key: str = ""  # the start of its records' keys (T1-12): set, or default_key(label)
    screens: dict = field(default_factory=dict)  # id -> {"id", "on": [ops], "tabs": [{"label", "fields": [names]}]}
    parent: str | None = None  # the path of a reference to its own type: its records form a tree
    part_of: dict | None = None  # {"field", "path", "target"}: the reference to the one record that owns it
    links: list[dict] = field(default_factory=list)  # [{rel, label, to, many, help, back}]: links a person may make
    slug: str = ""  # a part's segment in its address under its owner (/patients/7f3a/lab-results/b147); else its plural
    navigator: dict | None = None  # {"tops": words, "hide": [states]}: a second navigation column for the tree
    layout: str = "record"   # "page": its formatted text is the page, with its fields and controls in a bar above
    standard: str | None = None  # "fhir-r4": its records are FHIR R4 resources (exported as FHIR); None: its own shape
    consent: dict | None = None  # {"kind", "date", "source", "withdrawn": paths, "lasts": {kind: "2y" or ""}}
    booking: dict = field(default_factory=dict)  # a Booking type's [booking]: see _check_booking
    workflow: dict | None = None  # {"id", "when"}: the workflow a new record of it starts, about it (_check_workflow)
    unique: list[list[str]] = field(default_factory=list)  # keys (field names) no two of its records share (_check_unique)
    number: dict | None = None  # [number]: {field, path, pattern, date (a field's name, or None), when}: _check_number
    reverses: dict | None = None  # {field, path}: the reference to the final record of its type this one reverses
    covers: list[str] = field(default_factory=list)  # part types a signature on it covers, and its lock reaches
    period: dict | None = None  # [period]: {closed_by, date, path, scope, scope_path}: _check_period
    closing: dict | None = None  # [closing]: {through, path, states, scope, scope_path}: what closes a period
    checks: list[dict] = field(default_factory=list)  # [[check]]: {when, must, message, parts}: every write keeps them
    ancestors: tuple = ()  # the types above it, its parent first, up to the core base type at the root (if any)
    base: str | None = None  # the core base type at the root of its chain: what project and compose read it through
    doc: dict = field(default_factory=dict, repr=False, compare=False)  # its definition merged with its parents'
    chain: tuple = field(default=(), repr=False, compare=False)  # ((id, version, sha), ...): itself and its parents

    def screen(self, op: str) -> list[dict] | None:
        """The tabs of the screen shown to create, edit or view a record (None: no screen says, so every field)."""
        sc = next((s for s in self.screens.values() if op in s["on"]), None)
        return sc["tabs"] if sc else None

    def is_a(self, base: str) -> bool:
        """Whether its records may stand where ``base`` is asked for: itself, any type above it, or any record."""
        return base in ("*", ROOT) or base == self.id or base in self.ancestors

    def signature(self, meaning: str) -> dict | None:
        return next((g for g in self.signatures if g["meaning"] == meaning), None)

    def field(self, name: str) -> dict | None:
        return next((f for f in self.fields if f["name"] == name), None)

    def link(self, rel: str) -> dict | None:
        """The link a person may make from a record of it, by its rel: one it declares, or one of its reference fields
        (``{rel, label, to, many, field}``: made by filling in the field, never by hand)."""
        own = next((x for x in self.links if x["rel"] == rel), None)
        if own:
            return own
        f = next((f for f in self.references if f["rel"] == rel), None)
        return {"rel": rel, "label": f["label"], "to": list(f["target"]), "many": bool(f.get("list")) or "*" in f["path"],
                "field": f["name"], "help": f.get("help", ""), "back": ""} if f else None

    def allowed_links(self) -> list[dict]:
        """Every link a record of it may have: its reference fields', then those it declares to be made by hand."""
        seen, out = set(), []
        for x in [*(self.link(f["rel"]) for f in self.references), *self.links]:
            if x["rel"] not in seen:
                seen.add(x["rel"])
                out.append(x)
        return out

    def field_class(self, f: dict) -> str:
        """The data class that guards a field's value: its own, else the type's."""
        return f.get("data_class") or self.data_class

    @property
    def classed_fields(self) -> list[dict]:
        """The fields whose own data class differs from the type's: those some readers of the record do not see."""
        return [f for f in self.fields if f.get("data_class") and f["data_class"] != self.data_class]

    @property
    def references(self) -> list[dict]:
        """Its reference fields, a table's reference columns among them (at their paths: lines.*.item)."""
        return [f for f in self.fields + self.cells() if f["type"] == "reference"]

    def cells(self) -> list[dict]:
        """Its tables' columns as fields at their paths in the record (``lines.*.amount``), each naming its table."""
        return [{**c, "path": f"{t['path']}.*.{c['name']}", "table": t["name"], "by": t["by"],
                 "data_class": t.get("data_class")}
                for t in self.fields if t["type"] == "table" for c in t["columns"]]

    @property
    def graphs(self) -> list[dict]:
        """The references to its own records that never lead back to where they start (``graph``), a table's columns
        among them."""
        return [f for f in self.fields + self.cells() if f.get("graph")]

    @property
    def graph_groups(self) -> list[dict]:
        """The graph references by the words they are shown under: the fields that share them are walked as one
        (a lot built from lots, and a lot split off another, are both traced as what it came from). Each: ``down``,
        ``up``, ``fields`` and their ``rels``."""
        out: dict[tuple, dict] = {}
        for f in self.graphs:
            g = out.setdefault((f["graph"]["down"], f["graph"]["up"]),
                               {"down": f["graph"]["down"], "up": f["graph"]["up"], "fields": [], "rels": []})
            g["fields"].append(f)
            if f["rel"] not in g["rels"]:
                g["rels"].append(f["rel"])
        return list(out.values())

    def top_keys(self) -> set[str]:
        return {f["path"].split(".")[0] for f in self.fields} | RESERVED

    def states(self) -> list[str]:
        return list((self.lifecycle or {}).get("states") or [])

    def state_label(self, st) -> str:
        """A lifecycle state in words: its label, else its code with the dashes as spaces."""
        if st is None:
            return ""
        return ((self.lifecycle or {}).get("labels") or {}).get(st) or state_words(st)

    def transition(self, old, new) -> dict | None:
        for t in (self.lifecycle or {}).get("transition", []):
            if new == t["to"] and leaves(self.lifecycle, t, old):
                return t
        return None

    def is_final(self, st) -> bool:
        """Whether a record in state ``st`` is final (``lifecycle.final``): nothing changes it or moves it again."""
        return st is not None and st in ((self.lifecycle or {}).get("final") or [])


# A part's address is its owner's, then its slug and its id, to any depth (/patients/7f3a/visits/91c2/notes/44de).
# A slug is never one of the words that follow a record's address for its own pages and actions.
SLUG = re.compile(r"[a-z][a-z0-9-]*")
RESERVED_SEGMENTS = frozenset({"new", "export", "edit", "move", "sign", "attach", "override", "files", "workflow", "office",
                               "reschedule", "everything", "link", "unlink", "links"})


# Every key each table of a record type may hold: anything else is a typo, said so rather than quietly ignored.
KEYS = {
    "": {"id", "label", "plural", "data_class", "subject", "strict", "date", "flag", "alert", "active", "order",
         "field", "lifecycle", "summary", "signature", "abstract", "extends", "base", "title", "short_title", "screen", "parent",
         "part_of", "slug", "link", "key",
         "navigator", "layout", "standard", "consent", "booking", "workflow", "unique", "number", "reverses", "covers",
         "period", "closing", "check"},
    "field": {"name", "path", "type", "label", "required", "help", "target", "rel", "choices", "labels", "max_length", "min",
              "max", "above", "list", "show", "advanced", "with", "fact", "match", "by", "live", "data_class", "pin",
              "roles", "required_unless", "scale", "unit", "column", "min_rows", "max_rows", "from_file", "graph"},
    "lifecycle": {"field", "states", "initial", "starts", "transition", "locked", "final", "abandoned", "in_use", "labels",
                  "tones"},
    "transition": {"from", "to", "by", "label", "screen", "comment", "asks", "requires", "sign", "opens", "confirm",
                   "check", "stamp"},
    "check": {"when", "must", "message"},
    "signature": {"meaning", "roles", "states", "moves_to", "readers"},
    "link": {"rel", "label", "to", "many", "help", "back"},
    "screen": {"id", "on", "tab", "fields", "label"},
    "tab": {"label", "fields"},
    "navigator": {"tops", "hide"},
    "consent": {"kind", "date", "source", "withdrawn", "lasts"},
    "number": {"field", "pattern", "date", "when"},
    "period": {"closed_by", "date", "scope"},
    "closing": {"through", "states", "scope"},
    "booking": {"marked", "overbook", "overbook_label", "overbook_reason", "overbook_priority", "request",
                "request_inputs"},
}
FIELD_BOOLS = ("required", "list", "show", "advanced", "fact", "match", "live", "pin")
# A key that is another table's word for what this one calls something else, and is nowhere near it in spelling: a
# screen lists its ``fields``; a move ``asks`` for them.
OTHER_WORD = {"transition": {"fields": "asks"}}


def _unknown(where: str, table: dict, kind: str) -> list[str]:
    """``where: unknown key "requried" (did you mean "required"?)`` for each key the table may not hold."""
    out = []
    for k in table:
        if k not in KEYS[kind]:
            near = [w for w in [OTHER_WORD.get(kind, {}).get(k)] if w] \
                or difflib.get_close_matches(str(k), sorted(KEYS[kind]), n=1)
            out.append(f'{where}: unknown key "{k}"' + (f' (did you mean "{near[0]}"?)' if near else ""))
    return out


def _strings(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def default_label(name: str) -> str:
    """A label from a field name: ``serviceProvider`` and ``service_provider`` are both "Service provider"."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z][a-z])", " ", name).replace("_", " ").strip().capitalize()


def _spec(raw, i: int, table: str | None = None) -> tuple[dict | None, list[str]]:
    """One ``[[field]]``; ``table``: one column of that table field (``[[field.column]]``)."""
    if not isinstance(raw, dict):
        return None, [f"field {i + 1}: a field is a table ([[field]] with name = ..., type = ...)"]
    name = str(raw.get("name", ""))
    where = f"field {name or i + 1}" if table is None else f"field {table}, column {name or i + 1}"
    problems = _unknown(where, raw, "field")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        problems.append(f"{where}: a name is letters, digits and underscores")
    typ = raw.get("type", "string")
    if typ not in FIELD_TYPES:
        problems.append(f"{where}: unknown type {typ!r} (one of {', '.join(sorted(FIELD_TYPES))})")
    elif table is not None and typ not in COLUMN_TYPES:
        problems.append(f"{where}: a column is one value in each row ({', '.join(COLUMN_TYPES)}), not {typ}")
    if table is not None:
        problems += [f"{where}: {k} is said of the table, not of a column" for k in
                     ("path", "list", "by", "data_class", "pin", "column", "min_rows", "max_rows", "live", "fact",
                      "match", "advanced", "required_unless", "with", "from_file")
                     if k in raw]
    scale = raw.get("scale")
    if typ == "decimal":
        if isinstance(scale, bool) or not isinstance(scale, int) or not 0 <= scale <= decimals.MAX_SCALE:
            problems.append(f"{where}: a decimal keeps a set number of places: scale = 2 (a whole number from 0 to "
                            f"{decimals.MAX_SCALE})")
    elif scale is not None:
        problems.append(f"{where}: scale is for a decimal field")
    unit = raw.get("unit")
    if unit is not None and (not isinstance(unit, str) or not unit.strip() or len(unit) > 20):
        problems.append(f'{where}: unit is a few characters shown beside the value (at most 20), e.g. "%" or "kg"')
    elif unit is not None and typ not in ("number", "decimal"):
        problems.append(f"{where}: unit is for a number or decimal field")
    columns, rows = [], {}
    if typ == "table":
        raw_cols = raw.get("column")
        if not isinstance(raw_cols, list) or not raw_cols:
            problems.append(f"{where}: a table names its columns: [[field.column]] with name = ..., type = ...")
        else:
            for j, rc in enumerate(raw_cols):
                c, ps = _spec(rc, j, table=name)
                problems += ps
                if c:
                    columns.append(c)
            names = [c["name"] for c in columns]
            problems += [f"{where}: two columns are called {n}" for n in sorted({n for n in names if names.count(n) > 1})]
        for k in ("min_rows", "max_rows"):
            v = raw.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= MAX_ROWS):
                problems.append(f"{where}: {k} is a whole number of rows, at most {MAX_ROWS}")
            else:
                rows[k] = v
        if isinstance(rows.get("min_rows"), int) and isinstance(rows.get("max_rows"), int) \
                and rows["min_rows"] > rows["max_rows"]:
            problems.append(f"{where}: min_rows {rows['min_rows']} is above max_rows {rows['max_rows']}")
        if raw.get("list") is True:
            problems.append(f"{where}: a table holds its rows; it is not a list of tables")
        if "*" in str(raw.get("path") or ""):
            problems.append(f"{where}: a table is one field, not a path through a list")
    else:
        problems += [f"{where}: {k} is for a table field" for k in ("column", "min_rows", "max_rows") if k in raw]
    for k in FIELD_BOOLS:
        if k in raw and not isinstance(raw[k], bool):
            problems.append(f"{where}: {k} is true or false, not {raw[k]!r}")
    for k in ("label", "help", "rel", "data_class"):
        if k in raw and not isinstance(raw[k], str):
            problems.append(f"{where}: {k} is text, not {raw[k]!r}")
    ml = raw.get("max_length")
    if ml is not None and (isinstance(ml, bool) or not isinstance(ml, int) or ml < 1):
        problems.append(f"{where}: max_length is a whole number of characters, not {ml!r}")
    for k in ("min", "max", "above"):
        v = raw.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            problems.append(f"{where}: {k} is a number, not {v!r}")
        elif typ not in ("number", "decimal"):
            problems.append(f"{where}: {k} is for a number or decimal field")
    if isinstance(raw.get("min"), (int, float)) and isinstance(raw.get("max"), (int, float)) \
            and raw["min"] > raw["max"]:
        problems.append(f"{where}: min {raw['min']} is above max {raw['max']}")
    if raw.get("above") is not None and raw.get("min") is not None:
        problems.append(f"{where}: give min (that number or more) or above (more than it), not both")
    if isinstance(raw.get("above"), (int, float)) and isinstance(raw.get("max"), (int, float)) \
            and raw["above"] >= raw["max"]:
        problems.append(f"{where}: above {raw['above']} leaves nothing up to max {raw['max']}")
    choices = raw.get("choices")
    if choices is not None:
        if not _strings(choices):
            problems.append(f"{where}: choices is a list of words, e.g. [\"draft\", \"final\"]")
        elif len(set(choices)) != len(choices):
            problems.append(f"{where}: choices lists {next(c for c in choices if choices.count(c) > 1)!r} twice")
    if typ == "choice" and not choices:
        problems.append(f"{where}: a choice needs choices")
    labels = raw.get("labels")
    if labels is not None:
        if not isinstance(labels, dict) or not all(isinstance(v, str) and v.strip() for v in labels.values()):
            problems.append(f"{where}: labels is a table of choice = its words, e.g. {{ AMB = \"In clinic\" }}")
        elif not choices:
            problems.append(f"{where}: labels are the words of its choices; it has none")
        else:
            problems += [f"{where}: labels: {k!r} is not one of its choices" for k in labels
                         if k not in [str(c) for c in choices]]
    target = raw.get("target")
    if target is not None and not (isinstance(target, str) or _strings(target)):
        problems.append(f"{where}: target is a record type, or a list of them")
    elif typ == "reference" and not target:
        problems.append(f"{where}: a reference needs a target: the record types it may point at")
    elif target and typ != "reference":
        problems.append(f"{where}: target is for a reference field (this one is {typ})")
    if "with" in raw and not isinstance(raw["with"], dict):
        problems.append(f"{where}: with is a table of sibling keys and values, e.g. {{ system = \"phone\" }}")
    if raw.get("live") is True and typ != "markdown":
        problems.append(f"{where}: live is for a markdown field (several people write it at once)")
    if "from_file" in raw and not (isinstance(raw["from_file"], str) and raw["from_file"]):
        problems.append(f"{where}: from_file names the attachment field whose file's words fill this one")
    elif "from_file" in raw and typ != "text":
        problems.append(f"{where}: from_file is for a text field (it holds the words read from a file)")
    if raw.get("pin") is True and typ != "reference":
        problems.append(f"{where}: pin is for a reference field (it keeps the version of the record it points at)")
    elif raw.get("pin") is True and "*" in str(raw.get("path") or ""):
        problems.append(f"{where}: a pinned reference is one field (list = true for several), not a path through a list")
    if "roles" in raw and not (_strings(raw["roles"]) and raw["roles"]):
        problems.append(f"{where}: roles is a list of role ids, e.g. [\"lead\"]: only members holding one of them")
    elif "roles" in raw and typ != "reference":
        problems.append(f"{where}: roles is for a reference field that points at members (this one is {typ})")
    by = raw.get("by", "anyone")
    if by not in BY:
        problems.append(f"{where}: by is 'anyone' or 'workflow' (only a workflow step changes it, like stock on hand)")
    elif by == "workflow" and raw.get("required") is True:
        problems.append(f"{where}: a field only a workflow sets cannot be required: a person creating the record "
                        f"could never give it, so every new record would fail")
    unless = raw.get("required_unless")
    if unless is not None:
        if raw.get("required") is not True:
            problems.append(f"{where}: required_unless is for a required field: when the condition holds it may be "
                            f"left empty")
        elif not isinstance(unless, str) or not unless.strip():
            problems.append(f"{where}: required_unless is a condition over record, e.g. "
                            f"\"record.status == 'requested'\"")
        else:
            try:
                expr.check(unless)
            except expr.ExprError as exc:
                problems.append(f"{where}: required_unless: {exc}")
    path = raw.get("path") or name
    if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.([A-Za-z0-9_]+|\*))*", path):
        problems.append(f"{where}: path {path!r} is dotted keys and list positions, e.g. name.0.family")
    graph = raw.get("graph")
    if graph is not None:
        if not isinstance(graph, dict) or set(graph) != {"down", "up"} or not all(
                isinstance(v, str) and v.strip() for v in graph.values()):
            problems.append(f"{where}: graph is a table of the words for each way, e.g. "
                            f"{{ down = \"Bill of materials\", up = \"Used in\" }}")
        elif typ != "reference":
            problems.append(f"{where}: graph is for a reference field (this one is {typ})")
    if problems:
        return None, problems
    target = target or []
    f = {"name": name, "path": path, "type": typ, "label": raw.get("label") or default_label(name),
         "required": raw.get("required", False), "help": raw.get("help", ""),
         "target": [target] if isinstance(target, str) else list(target), "rel": raw.get("rel") or None,
         "choices": list(choices or []), "labels": dict(labels or {}), "max_length": ml, "min": raw.get("min"), "max": raw.get("max"), "above": raw.get("above"),
         "list": raw.get("list", False), "show": raw.get("show", True),
         "advanced": raw.get("advanced", False), "with": dict(raw.get("with") or {}),
         "fact": raw.get("fact", False), "match": raw.get("match", False), "by": by,
         "live": raw.get("live", False), "data_class": raw.get("data_class") or None,
         "roles": list(raw.get("roles") or []), "required_unless": unless or None, "pin": raw.get("pin") is True,
         "scale": scale if typ == "decimal" else None, "unit": unit or None, "from_file": raw.get("from_file") or None,
         "graph": dict(graph) if graph else None}
    if typ == "decimal":  # its bounds, exactly
        f["min"], f["max"], f["above"] = (None if v is None else decimals.to_decimal(v)
                                          for v in (f["min"], f["max"], f["above"]))
    if typ == "table":
        f.update(columns=columns, min_rows=rows.get("min_rows"), max_rows=rows.get("max_rows") or MAX_ROWS)
    if typ == "reference" and not f["rel"]:
        f["rel"] = name
    return f, []


def _check_graphs(rt) -> list[str]:
    """``graph``: a reference to the type's own records that never leads back to the record it starts from (a part
    made of parts, a lot built into lots), shown on a record's page both ways. It sits at a key, a list or a table's
    column. A type under another inherits its graph: it points at the family's records (the parent's type, or its
    own)."""
    problems = []
    words: dict[str, tuple] = {}
    for f in rt.graphs:
        if f["target"] != [rt.id] and not (len(f["target"]) == 1 and f["target"][0] in rt.ancestors):
            problems.append(f"field {f['name']}: graph is for a reference to its own type's records ({rt.id})")
        elif not (f.get("table") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.\*)?", f["path"])):
            problems.append(f"field {f['name']}: a graph's references sit at a key (next), a list (next.*) or a "
                            f"table's column")
        if f.get("data_class"):  # the trace shows every level to whoever reads the type
            problems.append(f"field {f['name']}: a graph is traced for whoever reads {rt.label.lower()} records: it "
                            f"has the type's data class, not one of its own")
        both = (f["graph"]["down"], f["graph"]["up"])
        for way in both:  # fields shown under the same words are walked as one: they share both ways' words
            if words.setdefault(way, both) != both:
                problems.append(f"field {f['name']}: {way!r} already names a way of another graph field, whose "
                                f"words are {' / '.join(words[way])}: graph fields shown together share both words")
    return problems


def parse(source: str, catalog=None, *, base_ok: bool = False, parent: RecordType | None = None) -> RecordType:
    """Parse and check a record type. ``catalog`` (the hub) checks the data class and reference targets exist, and
    gives the type it extends when that is one of the hub's. ``parent``: that type, given (read as in force, or
    another version of it being checked). ``base_ok``: the core's own base types, which keep no records and need no
    data class. Every problem is a DefinitionError, whatever shape the document has."""
    try:
        doc = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        raise DefinitionError([f"The record type is not valid TOML: {exc}"]) from None
    try:
        return _parse(doc, source, catalog, base_ok, parent)
    except DefinitionError:
        raise
    except (TypeError, ValueError, AttributeError, KeyError, IndexError) as exc:  # a shape no check below foresaw
        raise DefinitionError([f"the record type is not shaped as expected ({type(exc).__name__}: {exc})"]) from None


def _tables(doc: dict, key: str, problems: list, what: str) -> list[dict]:
    """``[[key]]``: a list of tables (anything else is a problem, and left out)."""
    v = doc.get(key)
    if v is None:
        return []
    if not isinstance(v, list):
        problems.append(f"{what}: a list of tables ([[{what}]])")
        return []
    bad = [x for x in v if not isinstance(x, dict)]
    if bad:
        problems.append(f"{what}: each one is a table ([[{what}]]), not {bad[0]!r}")
    return [x for x in v if isinstance(x, dict)]


def _resolves(path: str, fields: list[dict]) -> bool:
    """Whether a dotted path is a declared field's, inside one (an object or list it holds), or around some."""
    return any(path == f["path"] or path.startswith(f["path"] + ".") or f["path"].startswith(path + ".")
               for f in fields)


def move_row(frm, to, *, by, label, asks=(), requires=(), **more) -> dict:
    """One move of a lifecycle, as everything that reads a type's transitions takes it: a ``[[lifecycle.transition]]``
    as parsed, and a move the type's workflow makes (``catalog._with_workflow_moves``). ``asks``: the fields its
    screen asks for; ``requires``: those that must be set once it has moved. ``more``: its ``screen``, ``comment``,
    ``sign``, ``opens`` and ``confirm``, and what marks a move no transition was written for: a workflow's
    (``edge``, ``entry``), or where ``abandoned`` sends a record whose work is cancelled (``abandon``)."""
    return {"from": list(frm), "to": to, "by": by, "label": label, "screen": None, "asks": list(asks),
            "requires": list(requires), "comment": False, "sign": None, **more}


def leaves(lc: dict | None, t: dict, st) -> bool:
    """Whether the move ``t`` is made from state ``st``: a state it names, or any state (``*``) but a final one,
    which nothing leaves."""
    return st in t["from"] or ("*" in t["from"] and st not in ((lc or {}).get("final") or []))


def revising_states(states: list, trans: list, in_use, final=()) -> list:
    """The states a revision passes through: those with a way back to ``in_use`` (not 'obsolete')."""
    if in_use not in states:
        return []
    reach, grew = {in_use}, True
    while grew:
        grew = False
        for t in trans:
            for f in ([s for s in states if s not in final] if "*" in t["from"] else t["from"]):
                if t["to"] in reach and f not in reach:
                    reach.add(f)
                    grew = True
    return [x for x in states if x in reach and x != in_use]


def _parse(doc: dict, source: str, catalog, base_ok: bool, parent: RecordType | None) -> RecordType:
    problems: list[str] = _unknown("top level", doc, "")
    tid = str(doc.get("id", ""))
    if not TYPE_ID.fullmatch(tid) or tid.lower() == "new":
        problems.append("id: give the record type an id of letters, digits and dashes, starting with a letter")
    for k in ("subject", "strict", "alert", "abstract"):
        if k in doc and not isinstance(doc[k], bool):
            problems.append(f"{k}: true or false, not {doc[k]!r}")
    for k in ("label", "plural", "summary", "data_class", "title", "short_title", "active", "date", "flag", "parent", "extends",
              "part_of", "slug"):
        if k in doc and not isinstance(doc[k], str):
            problems.append(f"{k}: text, not {doc[k]!r}")
            doc.pop(k)
    abstract = doc.get("abstract") is True
    own, ext = doc, doc.get("extends")
    unresolved = False
    if ext is not None and ext not in bases() and not base_ok:  # one of the hub's types: its definition, then ours
        if parent is None or parent.id != ext:
            parent, why = catalog.type_parent(ext, tid) if catalog is not None else (None, None)
            if parent is None:
                unresolved = True
                problems.append(why or f"extends: {ext!r} is neither one of the core's base types "
                                       f"({', '.join(sorted(bases()))}) nor a record type in force here")
        if parent is not None:
            doc, more = _merge(parent, own)
            problems += more
    else:
        parent = None
    order = doc.get("order", 100)
    if isinstance(order, bool) or not isinstance(order, int):
        problems.append(f"order: a whole number (where it sits on a subject's page), not {order!r}")
        order = 100
    if not doc.get("data_class") and not base_ok and not unresolved:  # unresolved: its parent's, when it is read
        problems.append("data_class: say which data class the records belong to (permissions are per class)")
    fields, names, paths = [], set(), set()
    for i, raw in enumerate(_tables(doc, "field", problems, "field")):
        f, ps = _spec(raw, i)
        problems += ps
        if f:
            if f["name"] in names:
                problems.append(f"field {f['name']}: two fields share this name")
            if f["path"] in paths:
                problems.append(f"field {f['name']}: two fields share the path {f['path']}")
            names.add(f["name"])
            paths.add(f["path"])
            fields.append(f)
    by_name = {f["name"]: f for f in fields}
    for f in fields:  # filled with a file's words: from one file, in an attachment field of this type
        src = by_name.get(f["from_file"]) if f["from_file"] else None
        if f["from_file"] and not (src and src["type"] == "attachment" and not src["list"]
                                   and "*" not in src["path"] + f["path"]):
            problems.append(f"field {f['name']}: from_file names {f['from_file']!r}, which is not one of this type's "
                            f"attachment fields (one file, not a list, and neither field a path through a list)")
    if parent is not None:  # a field of its parent's it names again: narrowed, never changed
        problems += _narrowed(parent, own, fields, doc, catalog)
    ext, base_map, base = doc.get("extends"), {}, None  # under one of the hub's types: the core base at its root
    spec = doc.get("base")
    if spec is not None and not isinstance(spec, dict):
        problems.append("base: a table of base field = the field that holds it")
        spec = None
    if ext is not None and base_ok:
        problems.append("extends: a core base type extends nothing")
    elif ext is not None and ext in bases():
        base = bases()[ext]
        fields, base_map, ps = _inherit(base, fields, spec or {})
        problems += ps
    elif spec and ext is None:
        problems.append("base: says which fields hold a base type's fields; say which type it extends")
    seen: dict = {}
    for f in fields:  # a form with two "Status" boxes: nobody can tell which is which
        other = seen.setdefault(f["label"].strip().lower(), f["name"])
        if other != f["name"]:
            problems.append(f"field {f['name']}: {other} has the same label {f['label']!r}; give one a label of its own")
    if any(f["path"].split(".")[0] in RESERVED for f in fields):
        problems.append("field: resourceType, id and meta are kept by the hub")
    active = doc.get("active")
    if active:
        try:
            expr.check(active)
        except expr.ExprError as exc:
            problems.append(f"active: {exc}")
    lc = doc.get("lifecycle")
    if lc is not None and not isinstance(lc, dict):
        problems.append("lifecycle: a table ([lifecycle]) with its field, initial state and transitions")
        lc = None
    if lc is not None:
        problems += _unknown("lifecycle", lc, "lifecycle")
        lf = lc.get("field", "status")
        if not isinstance(lf, str):
            problems.append(f"lifecycle.field: the name of the field that holds the state, not {lf!r}")
            lf = "status"
        fd = next((f for f in fields if f["name"] == lf), None)
        if not fd:
            problems.append(f"lifecycle.field: there is no field {lf!r}")
        elif fd["by"] == "workflow":
            problems.append(f"lifecycle.field: {lf} says by = \"workflow\", which would stop every move, even those "
                            f"the transitions give to anyone; say by on each transition instead")
        if fd and fd.get("labels"):  # nothing that shows a state reads them: its words are said once
            problems.append(f"field {lf}: a state's words are lifecycle.labels (its workflow names its own states), "
                            f"not the field's labels")
        states = lc.get("states") or (fd or {}).get("choices") or []
        if not _strings(states):
            problems.append("lifecycle.states: a list of words")
            states = []
        states = list(states)
        if not states:
            problems.append("lifecycle: list its states (or make its field a choice)")
        labels = lc.get("labels") or {}  # a state's words where its code is not (onleave: "On leave")
        if not isinstance(labels, dict) or not all(isinstance(v, str) for v in labels.values()):
            problems.append("lifecycle.labels: a table of state = its words")
            labels = {}
        for st in labels:
            if st not in states:
                problems.append(f"lifecycle.labels: {st!r} is not one of its states")
        labels = {st: words for st, words in labels.items() if st in states and words}  # only what the type says
        initial = lc.get("initial")
        if initial is None:
            problems.append("lifecycle.initial: say which state a new record starts in")
        elif initial not in states:
            problems.append(f"lifecycle.initial: {initial!r} is not one of its states")
        trans = []
        for t in _tables(lc, "transition", problems, "lifecycle.transition"):
            frm = t.get("from", ["*"])
            frm = [frm] if isinstance(frm, str) else frm
            to = t.get("to")
            where = f"lifecycle.transition to {to!r}"
            problems += _unknown(where, t, "transition")
            if not _strings(frm) or not isinstance(to, str):
                problems.append(f"{where}: from is a state or a list of them, and to is one state")
                continue
            by = t.get("by", "anyone")
            for s in frm + [to]:
                if s != "*" and s not in states:
                    problems.append(f"lifecycle.transition: {s!r} is not one of its states")
            if by not in BY:
                problems.append(f"{where}: by is one of {', '.join(BY)}")
            asks, requires = ([v] if isinstance(v, str) else v or [] for v in (t.get("asks"), t.get("requires")))
            if not _strings(asks) or not _strings(requires):
                problems.append(f"{where}: asks and requires are lists of field names")
                asks, requires = [], []
            sign = t.get("sign")
            if sign is not None and not (isinstance(sign, str) and sign.strip()):
                problems.append(f"{where}: sign is what the mover's signature means, e.g. sign = \"Approved\"")
                sign = None
            if not isinstance(t.get("comment", False), bool):
                problems.append(f"{where}: comment is true or false")
            if t.get("screen") is not None and not isinstance(t["screen"], str):
                problems.append(f"{where}: screen is a screen id")
            opens = _transition_opens(t.get("opens"), where, problems)
            if t.get("confirm") is not None and not isinstance(t["confirm"], str):
                problems.append(f"{where}: confirm is what the person is told before the move, in words")
            stamp = t.get("stamp", [])
            stamp = [stamp] if isinstance(stamp, str) else stamp
            if not _strings(stamp):
                problems.append(f"{where}: stamp is a list of fields the move sets to who made it and when, "
                                f"e.g. stamp = [\"reviewed_by\", \"reviewed_on\"]")
                stamp = []
            trans.append(move_row(frm, to, by=by, label=str(t.get("label") or labels.get(to) or state_words(to)),
                                  asks=asks, requires=requires,
                                  screen=t.get("screen") if isinstance(t.get("screen"), str) else None,
                                  comment=t.get("comment") is True, sign=sign.strip() if sign else None,
                                  **({"opens": opens} if opens else {}),
                                  **({"confirm": t["confirm"]} if isinstance(t.get("confirm"), str) else {}),
                                  **({"check": t["check"]} if t.get("check") is not None else {}),
                                  **({"stamp": list(stamp)} if stamp else {})))
        # further states a person may make a new record in, besides the initial one (a record of work already done,
        # filed after the fact), each starting whatever the type's workflow starts for it
        starts = lc.get("starts") or []
        if not _strings(starts):
            problems.append("lifecycle.starts: a list of its states")
            starts = []
        for st in starts:
            if st not in states:
                problems.append(f"lifecycle.starts: {st!r} is not one of its states")
        locked = lc.get("locked") or []  # states in which people change nothing but the state itself
        if not _strings(locked):
            problems.append("lifecycle.locked: a list of its states")
            locked = []
        for st in locked:
            if st not in states:
                problems.append(f"lifecycle.locked: {st!r} is not one of its states")
        # States nothing changes or moves a record out of, by anyone (a workflow, a signature, an upgrade): a record
        # that is final is corrected by another one (``reverses``). A final state is locked too.
        final = lc.get("final") or []
        if not _strings(final):
            problems.append("lifecycle.final: a list of its states")
            final = []
        for st in final:
            if st not in states:
                problems.append(f"lifecycle.final: {st!r} is not one of its states")
        final = [st for st in final if st in states]
        for t in trans:
            for st in t["from"]:
                if st in final:
                    problems.append(f"lifecycle.transition to {t['to']!r}: nothing moves a record out of {st!r}, "
                                    f"which is final")
        locked = list(dict.fromkeys([*locked, *final]))
        # Where a record goes when the work that was moving it is cancelled: a state that only workflows leave
        # would otherwise strand the record for good. It is a move of its own, which the engine makes
        # (``Engine._abandon``): no transition is written for it. A state named as its own target stays where it is.
        abandoned = lc.get("abandoned") or {}
        if not isinstance(abandoned, dict):
            problems.append("lifecycle.abandoned: a table of state = the state it goes to")
            abandoned = {}
        abandoned = dict(abandoned)
        for st, to in abandoned.items():
            if st not in states or to not in states:
                problems.append(f"lifecycle.abandoned: {st!r} = {to!r}: both must be its states")
            elif st in final and to != st:
                problems.append(f"lifecycle.abandoned: {st!r} is final: a record stays there when its work is "
                                f"cancelled; leave it out")
        for st in states:
            exits = [t for t in trans if leaves({"final": final}, t, st) and t["to"] != st]
            if exits and all(t["by"] == "workflow" for t in exits) and st not in abandoned:
                problems.append(f"lifecycle.abandoned: only workflows move a record out of {st!r}; say where it goes "
                                f"if that work is cancelled (abandoned = {{ {st!r} = ... }})".replace("'", '"'))
        for t in trans:  # a move by the workflow that says nothing but what abandoned says already
            if t["by"] == "workflow" and not (t["asks"] or t["requires"] or t["sign"] or t["screen"]) \
                    and t["from"] and all(f in states and abandoned.get(f) == t["to"] for f in t["from"]):
                frm = [repr(f) for f in t["from"]]
                problems.append(f"lifecycle.transition to {t['to']!r}: abandoned already moves a record there from "
                                f"{' and '.join([', '.join(frm[:-1]), frm[-1]] if frm[:-1] else frm)} when its work "
                                f"is cancelled; leave it out")
        for st, to in abandoned.items():  # the move itself, for each pair no transition of the type's makes
            if st != to and st in states and to in states and st not in final \
                    and not any(x["to"] == to and leaves({"final": final}, x, st) for x in trans):
                trans.append(move_row([st], to, by="workflow", label=labels.get(to) or state_words(to), abandon=True))
        in_use = lc.get("in_use")  # the state people work from; while revised, the last such version stays shown
        if in_use is not None and in_use not in states:
            problems.append(f"lifecycle.in_use: {in_use!r} is not one of its states")
        revising = revising_states(states, trans, in_use, final)
        tones = _lifecycle_tones(lc.get("tones"), states, problems)
        lc = {"field": lf, "path": (fd or {}).get("path", lf), "states": states, "initial": initial, "tones": tones,
              "starts": list(starts), "transition": trans, "locked": locked, "final": final, "abandoned": abandoned,
              "in_use": in_use, "revising": revising, "labels": labels}
    for key in ("date", "flag"):
        p = doc.get(key)
        if p and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.([A-Za-z0-9_]+))*", str(p)):
            problems.append(f"{key}: {p!r} is a dotted path into the record")
        elif p and not _resolves(p, fields):
            problems.append(f"{key}: {p!r} is not the path of any of its fields")
    sigs = []
    for g in _tables(doc, "signature", problems, "signature"):
        meaning = str(g.get("meaning", "")).strip()
        problems += _unknown(f"signature {meaning or len(sigs) + 1!r}", g, "signature")
        if not meaning:
            problems.append("signature: say what signing means, e.g. 'Approved for release'")
            continue
        states = g.get("states", []) or []
        states = [states] if isinstance(states, str) else states
        roles = g.get("roles", []) or []
        roles = [roles] if isinstance(roles, str) else roles
        if not _strings(states) or not _strings(roles):
            problems.append(f"signature {meaning!r}: states and roles are lists of words")
            continue
        for st in states:
            if lc is not None and st not in lc["states"]:
                problems.append(f"signature {meaning!r}: {st!r} is not one of its states")
        sigs.append({"meaning": meaning, "roles": list(roles), "states": list(states), "moves_to": g.get("moves_to"),
                     "readers": g.get("readers") is True})  # readers: everyone who reads it is asked to sign it
        if "readers" in g and not isinstance(g["readers"], bool):
            problems.append(f"signature {meaning!r}: readers is true or false")
        if g.get("moves_to") and (lc is None or g["moves_to"] not in lc["states"]):
            problems.append(f"signature {meaning!r}: moves_to {g['moves_to']!r} is not one of its states")
        if g.get("moves_to") and not g.get("roles"):
            problems.append(f"signature {meaning!r}: a signature that moves the record says which roles may sign it")
    for t in (lc or {}).get("transition", []):
        if t.get("sign") and sigs and not any(g["meaning"] == t["sign"] for g in sigs):
            problems.append(f"lifecycle.transition to {t['to']!r}: sign {t['sign']!r} is not one of its signatures' "
                            f"meanings ({', '.join(repr(g['meaning']) for g in sigs)})")
    screens = _screens(doc, fields, base_map, lc, problems)
    label = str(doc.get("label") or tid)
    rt = RecordType(id=tid, label=label, plural=str(doc.get("plural") or plural_of(label)),
                    data_class=str(doc.get("data_class", "")), fields=fields, subject=doc.get("subject") is True,
                    strict=doc.get("strict", True) is not False, date=doc.get("date"), flag=doc.get("flag"),
                    alert=doc.get("alert") is True, active=active, order=order,
                    lifecycle=lc, summary=str(doc.get("summary", "")), source=source, signatures=sigs,
                    abstract=abstract, extends=parent.id if parent else base.id if base else None, base_map=base_map,
                    title=doc.get("title"), short_title=doc.get("short_title"), screens=screens,
                    ancestors=(parent.id, *parent.ancestors) if parent else (base.id,) if base else (),
                    base=base.id if base else None, doc=doc)
    if "key" in doc:
        if isinstance(doc["key"], str) and TYPE_KEY.fullmatch(doc["key"]):
            rt.key = doc["key"]
        else:
            problems.append(f"key: one to eight capital letters and digits, starting with a letter (T1, ENG), not "
                            f"{doc['key']!r}")
    if not rt.key:
        rt.key = default_key(label)
    if base is not None:
        rt.date = rt.date or base.date
        rt.subject = doc.get("subject", base.id == "Subject") is True
    for key in ("title", "short_title"):
        tmpl = getattr(rt, key)
        if not tmpl:
            continue
        problems += [f"{key}: {p}" for p in _check_title(tmpl)]
        known = {f["name"] for f in fields if "*" not in f["path"]} | set(base_map)
        for n in TITLE_PART.findall(tmpl):
            if n not in known:
                near = difflib.get_close_matches(n, sorted(known), n=1)
                problems.append(f"{key}: {{{n}}} is not one of its fields" + (f" (did you mean {{{near[0]}}}?)"
                                                                           if near else ""))
    kin = {tid, *rt.ancestors}  # its own kind: itself and the types above it
    if doc.get("parent"):
        pf = rt.field(str(doc["parent"]))
        if not pf or pf["type"] != "reference" or pf.get("list") or "*" in pf["path"] or not kin & set(pf["target"]):
            problems.append(f"parent: {doc['parent']!r} is not a field that refers to one other {tid}")
        else:
            rt.parent = pf["path"]
    if doc.get("part_of"):
        name = str(doc["part_of"])
        own = ((base_map.get(name) or {}).get("fields") or [name])[0] if name in base_map else name
        pf = rt.field(own)
        if not pf or pf["type"] != "reference" or pf.get("list") or "*" in pf["path"]:
            problems.append(f"part_of: {name!r} is not a field that refers to one record (the one that owns it)")
        elif not pf.get("required"):
            problems.append(f"part_of: {name!r} must be required: a {rt.label.lower()} never exists without its owner")
        elif kin & set(pf["target"]):
            problems.append("part_of: a record is not part of its own kind; for a tree, say parent")
        else:
            rt.part_of = {"field": pf["name"], "path": pf["path"], "target": list(pf["target"])}
    problems += _check_graphs(rt)
    rt.slug = str(doc.get("slug") or re.sub(r"[^a-z0-9]+", "-", rt.plural.lower()).strip("-"))
    if not SLUG.fullmatch(rt.slug) or rt.slug in RESERVED_SEGMENTS:
        problems.append(f"slug: {rt.slug!r} cannot be a part of an address: give one of lowercase letters, digits and "
                        f"dashes, starting with a letter, and not one of {', '.join(sorted(RESERVED_SEGMENTS))}")
    rt.links, more = _check_links(doc.get("link", []), rt)
    problems += more
    problems += _check_field_classes(rt)
    nav = doc.get("navigator")
    if nav is not None:
        hide = nav.get("hide", []) if isinstance(nav, dict) else None
        states = set((rt.lifecycle or {}).get("states", [])) or set(
            next((f.get("choices", []) for f in rt.fields if rt.lifecycle and f["path"] == rt.lifecycle["path"]), []))
        if not isinstance(nav, dict):
            problems.append("navigator: a table of tops (words) and hide (states)")
        elif set(nav) - KEYS["navigator"]:
            problems += _unknown("navigator", nav, "navigator")
        elif not doc.get("parent"):
            problems.append("navigator: only a type whose records form a tree (parent) has one")
        elif not isinstance(hide, list) or any(not isinstance(h, str) or (states and h not in states) for h in hide):
            problems.append(f"navigator.hide: states of its lifecycle ({', '.join(sorted(states)) or 'none'})")
        else:
            rt.navigator = {"tops": str(nav.get("tops") or rt.plural)[:40], "hide": list(hide)}
    layout = doc.get("layout", "record")
    if layout not in ("record", "page"):
        problems.append(f"layout: 'record' or 'page', not {layout!r}")
    elif layout == "page" and not any(f["type"] == "markdown" for f in rt.fields):
        problems.append("layout: a page is formatted text; give the type a field of type markdown")
    else:
        rt.layout = layout
    if doc.get("consent") is not None:
        problems += _check_consent(rt, doc["consent"])
    if doc.get("booking") is not None:
        problems += _check_booking(rt, doc["booking"])
    if doc.get("workflow") is not None:
        problems += _check_workflow(rt, doc["workflow"])
    if doc.get("unique") is not None:
        problems += _check_unique(rt, doc["unique"])
    if doc.get("number") is not None:
        problems += _check_number(rt, doc["number"])
    if doc.get("reverses") is not None:
        problems += _check_reverses(rt, doc["reverses"])
    if doc.get("covers") is not None:
        problems += _check_covers(rt, doc["covers"])
    if doc.get("period") is not None:
        problems += _check_period(rt, doc["period"])
    if doc.get("closing") is not None:
        problems += _check_closing(rt, doc["closing"])
    if doc.get("check") is not None:
        rt.checks, more = _check_checks(rt, doc["check"], "check")
        problems += more
    for t in (rt.lifecycle or {}).get("transition", []):  # a move's checks read fields the type now has
        if t.get("check"):
            t["check"], more = _check_checks(rt, t["check"], f"lifecycle.transition to {t['to']!r}: check",
                                             parts=True)
            problems += more
    std = doc.get("standard")
    if std is not None:
        problems += _check_standard(rt, std)
        if std in STANDARDS:
            rt.standard = std
    if catalog is not None:
        try:
            problems += [p for p in catalog.check_type(rt) if p not in problems]
        except Exception:  # noqa: BLE001 - the type's own problems say why
            pass
    if problems:
        raise DefinitionError(problems)
    return rt


def _check_workflow(rt: RecordType, w) -> list[str]:
    """``workflow``: the workflow that moves a record of this type from open to closed. Creating a record starts it,
    about that record, however it is created (a form, the calendar, the API, another workflow's step); the record's
    lifecycle state is where that work is. Sets ``rt.workflow`` when sound.

        workflow = "order-handling"
        workflow = { id = "order-handling", when = "record.status in ['requested', 'booked']" }   # only these start it
        workflow = { id = "lab-result-review", again = "record.status == 'corrected'" }

    ``again``: a write that does not move the record's state (a lab writing status = "corrected" on a result already
    reviewed) and makes this condition hold, where it did not before, starts the workflow about the record again,
    from where it is, when no run of it is walking it (``starts_again``). ``when`` does not apply to it.
    """
    if isinstance(w, str):
        w = {"id": w}
    if not isinstance(w, dict) or set(w) - {"id", "when", "again"} or not isinstance(w.get("id"), str) \
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", w.get("id") or ""):
        return ["workflow: the id of a workflow, e.g. \"order-handling\", or { id = ..., when = \"<condition over "
                "record>\" }"]
    conds = {}
    for key in ("when", "again"):
        cond = w.get(key)
        if cond is None:
            continue
        if not isinstance(cond, str) or not cond.strip():
            return [f"workflow.{key}: a condition over record, e.g. \"record.status == 'requested'\""]
        try:
            expr.check(cond)
        except expr.ExprError as exc:
            return [f"workflow.{key}: {exc}"]
        conds[key] = cond
    if not rt.lifecycle:
        return ["workflow: a record moved by a workflow has a lifecycle: its state is where the work is"]
    rt.workflow = {"id": w["id"], "when": conds.get("when"), **({"again": conds["again"]} if "again" in conds else {})}
    return []


UNIQUE_TYPES = ("string", "number", "decimal", "date", "datetime", "choice", "member", "reference")


def _check_unique(rt: RecordType, raw) -> list[str]:
    """``unique``: values no two records of the type share. Each key is a field, or a list of fields that together
    are the key (a serial is unique within its part). Sets ``rt.unique`` when sound.

        unique = ["code"]
        unique = ["code", ["part", "serial"]]

    A key is checked when a record is written with every one of its fields filled in; a record that leaves one empty
    (a part with no serials) shares nothing. Text is compared as a search compares it: "GL-M", "gl-m" and " GL-M"
    are one code."""
    if not isinstance(raw, list) or not raw:
        return ['unique: a list of fields, or of lists of fields that together are unique, e.g. ["code"] or '
                '[["part", "serial"]]']
    keys, problems = [], []
    for k in raw:
        names = [k] if isinstance(k, str) else k
        if not _strings(names) or not names:
            problems.append(f"unique: {k!r} is a field name, or a list of field names")
            continue
        if len(set(names)) != len(names):
            problems.append(f"unique: {names!r} names a field twice")
            continue
        for n in names:
            f = rt.field(n)
            if f is None:
                near = difflib.get_close_matches(n, [x["name"] for x in rt.fields], n=1)
                problems.append(f"unique: there is no field {n!r}" + (f' (did you mean "{near[0]}"?)' if near else ""))
            elif f.get("list") or "*" in f["path"]:
                problems.append(f"unique: {n} holds several values; a key is one value per record")
            elif f["type"] not in UNIQUE_TYPES:
                problems.append(f"unique: {n} is {f['type']}; a key is one of {', '.join(UNIQUE_TYPES)}")
        if sorted(names) in [sorted(x) for x in keys]:
            problems.append(f"unique: {names!r} is listed twice")
        keys.append(list(names))
    if not problems:
        rt.unique = keys
    return problems


def _check_number(rt: RecordType, raw) -> list[str]:
    """``[number]``: the number the hub gives each record of the type once, in a series with no gaps. Sets
    ``rt.number`` when sound, and marks its field as written by no one's hand (off every form)."""
    if not isinstance(raw, dict):
        return ['number: a table naming its field and pattern, e.g. { field = "number", pattern = "INV-{seq:04}" }']
    out = _unknown("number", raw, "number")
    f = rt.field(str(raw.get("field") or ""))
    if f is None:
        out.append(f"number.field: name the string field that holds it ({raw.get('field')!r} is not one of its fields)")
    elif f["type"] != "string" or f.get("list") or "*" in f["path"]:
        out.append(f"number.field: {f['name']} is a {f['type']} field{' of several values' if f.get('list') else ''}"
                   f"; a number is held in a string field, one per record")
    elif f.get("required"):
        out.append(f"number.field: {f['name']} is not required: the hub writes it, when the record is numbered")
    elif rt.lifecycle and f["path"] == rt.lifecycle["path"]:
        out.append(f"number.field: {f['name']} holds the record's state")
    pattern = raw.get("pattern")
    if not isinstance(pattern, str) or not pattern or len(pattern) > 40:
        out.append('number.pattern: text of at most 40 characters, e.g. "INV-{year}-{seq:04}"')
        pattern = ""
    elif sum(1 for m in NUMBER_TOKEN.finditer(pattern) if m.group(1).startswith("seq")) != 1:
        out.append(f"number.pattern: {pattern!r} names {{seq}} (the number in its series) once, e.g. "
                   f"\"INV-{{seq:04}}\"")
    elif not NUMBER_TEXT.fullmatch(NUMBER_TOKEN.sub("", pattern)) or NUMBER_TOKEN.sub("", pattern).count("{"):
        out.append(f"number.pattern: {pattern!r}: between {{seq}}, {{year}} and {{month}}, only letters, digits and "
                   f"- / . _")
    dated = None
    if raw.get("date") is not None:
        df = rt.field(str(raw["date"]))
        if df is None or df["type"] not in ("date", "datetime") or df.get("list") or "*" in df["path"]:
            out.append(f"number.date: name its date field ({raw['date']!r} is not one)")
        else:
            dated = df["name"]
    else:
        dated = next((x["name"] for x in rt.fields if rt.date and x["path"] == rt.date
                      and x["type"] in ("date", "datetime") and not x.get("list")), None)
        if pattern and number_reads_date(pattern) and not dated:
            out.append(f"number.date: {pattern!r} names the year or month: say which date field it reads")
    when = raw.get("when", [])
    states = (rt.lifecycle or {}).get("states") or []
    if not _strings(when):
        out.append("number.when: a list of states")
        when = []
    elif when and not rt.lifecycle:
        out.append("number.when: states of its lifecycle, and it has none")
    for st in when if rt.lifecycle else []:
        if st not in states:
            out.append(f"number.when: {st!r} is not one of its states ({', '.join(states)})")
    if not out:
        f["by"] = "workflow"  # off every form and every tool's input: the hub alone writes it
        rt.number = {"field": f["name"], "path": f["path"], "pattern": pattern, "date": dated, "when": list(when)}
    return out


def plural_of(label: str) -> str:
    """A label's plural when the type gives none: Entry, Entries; Box, Boxes; Visit, Visits."""
    if re.search(r"[^aeiou]y$", label):
        return label[:-1] + "ies"
    return label + ("es" if re.search(r"(s|x|z|ch|sh)$", label) else "s")


def _one_field(rt: RecordType, name, what: str, types: tuple, key: str) -> tuple[dict | None, str | None]:
    """The field ``name`` names, when it is one of ``types`` and holds one value per record; else why not."""
    f = rt.field(name) if isinstance(name, str) else None
    if f is None:
        near = difflib.get_close_matches(str(name), [x["name"] for x in rt.fields], n=1)
        return None, f"{key}: there is no field {name!r}" + (f' (did you mean "{near[0]}"?)' if near else "")
    if f["type"] not in types or f.get("list") or "*" in f["path"]:
        return None, f"{key}: {f['name']} is {'a list of ' if f.get('list') else ''}{f['type']}; it is {what}"
    return f, None


def _check_reverses(rt: RecordType, raw) -> list[str]:
    """``reverses = "<field>"``: the reference by which a record of the type reverses another one of it, one that
    is final (a posted entry is corrected by an entry that reverses it, never edited). A record is reversed once, never
    by itself, and the field is given when the reversing record is made and never changes (``Hub._check_reversal``).
    What the reversing record says (amounts the other way, say) is the pack's: the core copies, never negates."""
    f, why = _one_field(rt, raw, f"a reference to one other {rt.id}", ("reference",), "reverses")
    if why:
        return [why]
    out = []
    if f["target"] != [rt.id]:
        out.append(f"reverses: {f['name']} refers to {' or '.join(f['target'])}; a record reverses one of its own "
                   f"type ({rt.id})")
    if f.get("required"):
        out.append(f"reverses: {f['name']} is not required: only a record that reverses another one gives it")
    if not (rt.lifecycle or {}).get("final"):
        out.append("reverses: a record is reversed once it is final; say which states are (lifecycle.final)")
    if not out:
        rt.reverses = {"field": f["name"], "path": f["path"]}
    return out


def _check_covers(rt: RecordType, raw) -> list[str]:
    """``covers = ["<part type>"]``: the types of the records that are part of this one (their ``part_of`` points
    here) that a signature on it covers, besides its own contents and the records it pins: a change to one leaves
    the signature not current. While the record is locked people do not add or change them; while it is final, no
    one does. The catalog checks each is such a part (``CatalogMixin.check_type``)."""
    if not _strings(raw) or not raw or len(set(raw)) != len(raw):
        return ['covers: a list of the record types that are part of it, each once, e.g. ["Line"]']
    if rt.id in raw:
        return ["covers: a record is not part of itself"]
    rt.covers = list(raw)
    return []


def _date_of_period(rt: RecordType, raw: dict, key: str) -> tuple[dict | None, list[str]]:
    if raw.get("date") is not None:
        f, why = _one_field(rt, raw["date"], "a date", ("date", "datetime"), f"{key}.date")
    else:
        f = next((x for x in rt.fields if rt.date and x["path"] == rt.date and x["type"] in ("date", "datetime")
                  and not x.get("list")), None)
        why = None if f else f"{key}.date: name the date field that puts a record in a period"
    if why:
        return None, [why]
    if not f.get("required"):
        return None, [f"{key}.date: {f['name']} is required: a record with no date would be in no period"]
    return f, []


def _scope_of(rt: RecordType, raw: dict, key: str) -> tuple[dict | None, list[str]]:
    if raw.get("scope") is None:
        return None, []
    f, why = _one_field(rt, raw["scope"], "a reference to one record", ("reference",), f"{key}.scope")
    return (None, [why]) if why else (f, [])


def _check_period(rt: RecordType, raw) -> list[str]:
    """``[period]``: records of the type are dated in periods that a record of another type closes (its
    ``[closing]``). Nothing dated on or before the latest date closed is written, by anyone: not a person, a
    workflow, a signature or an upgrade (``Hub._check_period``), until a signed move reopens it.

        [period]
        closed_by = "PeriodClose"   # the type whose records close it
        date = "issued_on"          # optional (default: the type's date); a required date field
        scope = "account"           # optional: a reference; a close reaches only records with the same one"""
    if not isinstance(raw, dict):
        return ['period: a table, e.g. [period] closed_by = "PeriodClose"']
    out = _unknown("period", raw, "period")
    if not isinstance(raw.get("closed_by"), str) or not TYPE_ID.fullmatch(raw["closed_by"]):
        out.append("period.closed_by: the record type whose records close a period")
    elif raw["closed_by"] == rt.id:
        out.append("period.closed_by: a record does not close its own period; name the type that does")
    df, more = _date_of_period(rt, raw, "period")
    sf, more2 = _scope_of(rt, raw, "period")
    out += more + more2
    if not out:
        rt.period = {"closed_by": raw["closed_by"], "date": df["name"], "path": df["path"],
                     "scope": sf["name"] if sf else None, "scope_path": sf["path"] if sf else None,
                     "scope_target": list(sf["target"]) if sf else []}
    return out


def _check_closing(rt: RecordType, raw) -> list[str]:
    """``[closing]``: a record of the type closes a period of the types whose ``[period]`` names it, through its
    ``through`` date, while it is in one of ``states``. Nothing changes it while it is, and it leaves them only by a
    move its mover signs (``sign``): reopening a period is a signed decision.

        [closing]
        through = "through"     # its required date field: the last day closed
        states = ["closed"]     # it closes while in these
        scope = "account"       # as the [period] says: both have one, or neither"""
    if not isinstance(raw, dict):
        return ['closing: a table, e.g. [closing] through = "through", states = ["closed"]']
    out = _unknown("closing", raw, "closing")
    tf, why = _one_field(rt, raw.get("through"), "the last date it closes", ("date",), "closing.through")
    if why:
        out.append(why)
    elif not tf.get("required"):
        out.append(f"closing.through: {tf['name']} is required: a close with no date closes nothing")
    lc = rt.lifecycle
    states = raw.get("states")
    if not lc:
        out.append("closing: a record closes a period while it is in some states: give the type a lifecycle")
    elif not _strings(states) or not states:
        out.append("closing.states: the states in which it closes, e.g. [\"closed\"]")
    else:
        for st in states:
            if st not in lc["states"]:
                out.append(f"closing.states: {st!r} is not one of its states")
            elif st in (lc.get("final") or []):
                out.append(f"closing.states: {st!r} is final, so the period would never reopen; leave it out of "
                           f"lifecycle.final")
        for t in lc["transition"]:
            for st in states:
                if leaves(lc, t, st) and t["to"] not in states and not t.get("sign"):
                    out.append(f"lifecycle.transition to {t['to']!r}: it reopens a closed period, from {st!r}; its "
                               f"mover signs it (sign = \"...\")")
                    break
    sf, more = _scope_of(rt, raw, "closing")
    out += more
    if not out:
        rt.closing = {"through": tf["name"], "path": tf["path"], "states": list(states),
                      "scope": sf["name"] if sf else None, "scope_path": sf["path"] if sf else None,
                      "scope_target": list(sf["target"]) if sf else []}
        lc["locked"] = list(dict.fromkeys([*lc["locked"], *states]))
    return out


def _check_checks(rt: RecordType, raw, where: str, *, parts: bool = False) -> tuple[list[dict], list[str]]:
    """``[[check]]`` (a rule every write of a record keeps) and ``[[lifecycle.transition.check]]`` (one a move
    keeps): what ``must`` hold, a condition over the record (``record.<field>``, by the field's name) and ``today``,
    while ``when`` does (none: always), and the ``message`` that says why not. A move's check may also read its
    record's parts (``parts.<part type>``: a list of ``{record, signed}``, ``signed`` being the meanings of the
    part's current signatures); a record's check reads only the record, since a part is written apart from it.

        [[check]]
        when = "record.status != 'draft'"
        must = "record.total == record.amount + sum(record.lines.*.amount)"
        message = "the total is the amount and its lines"

    Returns the checks (``{when, must, message, parts}``: the part types it reads) and the problems."""
    problems: list[str] = []
    out = []
    for i, c in enumerate(_tables({"check": raw}, "check", problems, where)):
        at = f"{where} {i + 1}"
        problems += _unknown(at, c, "check")
        must, when, msg = c.get("must"), c.get("when"), c.get("message")
        if not isinstance(msg, str) or not msg.strip():
            problems.append(f"{at}: message says, in words, what does not hold (\"the total is the amount plus "
                            f"its lines\")")
        if not isinstance(must, str) or not must.strip():
            problems.append(f"{at}: must is the condition that holds, e.g. must = \"record.end >= record.start\"")
            continue
        if when is not None and not (isinstance(when, str) and when.strip()):
            problems.append(f"{at}: when is a condition, e.g. when = \"record.status != 'draft'\"")
            continue
        read: set[str] = set()
        for key, src in (("must", must), ("when", when)):
            if src is None:
                continue
            try:
                problems += [f"{at}: {key}: {p}" for p in _check_rule_paths(rt, expr.paths(src), parts, read)]
            except expr.ExprError as exc:
                problems.append(f"{at}: {key}: {exc}")
        out.append({"when": when, "must": must, "message": str(msg or "").strip(), "parts": sorted(read)})
    return out, problems


def _check_rule_paths(rt: RecordType, paths: list[str], parts: bool, read: set) -> list[str]:
    """What is wrong with the paths a check reads: ``record.<field>`` (a table's column after ``*``), ``today``, and
    for a move's check ``parts.<part type>.*`` with ``record.<field>`` or ``signed`` (the catalog checks the part
    type and its fields). ``read`` gathers the part types named."""
    out = []
    names = [f["name"] for f in rt.fields]
    for p in paths:
        bits = p.split(".")
        if p == "today":
            continue
        if bits[0] == "record":
            if len(bits) < 2:
                out.append("record alone is the whole record: read one field of it, record.<field>")
                continue
            f = rt.field(bits[1])
            if f is None:
                near = difflib.get_close_matches(bits[1], names, n=1)
                out.append(f"{p}: there is no field {bits[1]!r}" + (f' (did you mean "{near[0]}"?)' if near else ""))
            elif f["type"] == "table" and len(bits) > 3 and bits[2] == "*":
                cols = [c["name"] for c in f["columns"]]
                if bits[3] not in cols:
                    near = difflib.get_close_matches(bits[3], cols, n=1)
                    out.append(f"{p}: {f['name']} has no column {bits[3]!r}"
                               + (f' (did you mean "{near[0]}"?)' if near else ""))
            continue
        if bits[0] == "parts":
            if not parts:
                out.append(f"{p}: parts are read only by a move's check ([[lifecycle.transition.check]]): a part "
                           f"is written apart from its record")
            elif len(bits) < 3 or bits[2] != "*" or (len(bits) > 3 and bits[3] not in ("record", "signed")) \
                    or (len(bits) > 3 and bits[3] == "record" and len(bits) < 5):
                out.append(f"{p}: a record's parts are read as parts.<part type>.*, each with its record.<field> "
                           f"and signed, e.g. count(parts.Point.*, it.record.status != 'closed')")
            else:
                read.add(bits[1])
            continue
        more = ", and parts.<part type>" if parts else ""
        out.append(f"{p}: a check reads record.<field> and today{more}")
    return out


def check_holds(c: dict, ctx: dict) -> bool:
    """Whether a check holds of its context: its ``when`` does not, or its ``must`` does."""
    return not expr.holds(c.get("when"), ctx) or expr.holds(c["must"], ctx)


def check_context(rt: RecordType, rec: dict, today: str, parts: dict | None = None) -> dict:
    """What a check reads: the record by its field names and as stored, today, and (for a move's) its parts."""
    return {"record": {**_named_values(rt, rec), **rec}, "today": today, **({"parts": parts} if parts else {})}


def sealed(rt: RecordType, st) -> bool:
    """Whether a record in state ``st`` changes for no one, a workflow, a signature and an upgrade included: it is
    final, or it closes a period (it leaves that state only by a signed move)."""
    return rt.is_final(st) or (st is not None and st in ((rt.closing or {}).get("states") or []))


def pins(rt: RecordType) -> list[dict]:
    """Its reference fields that keep the version of the record they point at (``pin = true``)."""
    return [f for f in rt.fields if f["type"] == "reference" and f.get("pin")]


def starts_workflow(rt: RecordType, rec: dict) -> str | None:
    """The workflow a new record of ``rt`` starts (its type's ``workflow``, when its ``when`` holds of it). The
    condition reads the record as stored and by its type's field names (``record.category_text`` for a field at
    ``category.0.text``). A record in a final state starts nothing: no work changes it."""
    w = rt.workflow
    if not w or (w.get("when") and not expr.holds(w["when"], {"record": {**_named_values(rt, rec), **rec}})):
        return None
    if rt.lifecycle and sealed(rt, first(rec, rt.lifecycle["path"])):
        return None
    return w["id"]


def starts_again(rt: RecordType, before: dict, after: dict) -> str | None:
    """The workflow a write of a record that did not move it starts again (its type's ``workflow.again``, which
    holds of the record as written and did not hold of it before), or None."""
    w = rt.workflow or {}
    cond = w.get("again")
    if not cond or before is None or (rt.lifecycle and sealed(rt, first(after, rt.lifecycle["path"]))):
        return None
    ctx = lambda rec: {"record": {**_named_values(rt, rec), **rec}}  # noqa: E731
    if expr.holds(cond, ctx(after)) and not expr.holds(cond, ctx(before)):
        return w["id"]
    return None


def _named_values(rt: RecordType, rec: dict) -> dict:
    """The record's values by its type's field names, for a field whose path is not its name (its first value)."""
    out = {}
    for f in rt.fields:
        path = f.get("path")
        if path and path != f["name"]:
            vals = get(rec, path)
            if vals:
                out[f["name"]] = vals[0]
    return out


def _check_booking(rt: RecordType, b) -> list[str]:
    """``[booking]``: how the calendar books a record of a type that extends ``Booking``. Its words and roles are the
    practice's; the core only applies them. Sets ``rt.booking`` when sound.

        marked = ["urgent"]                 # the priorities (base field priority) the calendar marks on a booking
        overbook = ["physician"]            # the roles that may book over another booking, saying why
        overbook_label = "Squeeze in as urgent"   # the booking form's words for doing so
        overbook_reason = "Why it cannot wait"    # ...and for the reason they give (kept in base field overbooked)
        overbook_priority = "urgent"        # the priority a booking made over another one gets
        request = "appointment-request"     # a member who may not give the reason books through this workflow
        request_inputs = ["kind", "reason"] # the workflow's inputs the booking form asks for (it is given the slot)
    """
    if not isinstance(b, dict):
        return ["booking: a table (marked, overbook, overbook_label, overbook_reason, overbook_priority, request)"]
    out = _unknown("booking", b, "booking")
    if not rt.is_a("Booking"):
        out.append("booking: only a type that extends Booking says how it is booked")
    for k in ("marked", "overbook", "request_inputs"):
        if k in b and not _strings(b[k]):
            out.append(f"booking.{k}: a list of words")
    for k in ("overbook_label", "overbook_reason", "overbook_priority", "request"):
        if k in b and (not isinstance(b[k], str) or not b[k].strip()):
            out.append(f"booking.{k}: text")
    if b.get("overbook") and not (b.get("overbook_label") and b.get("overbook_reason")):
        out.append("booking: a type that lets members book over another booking says how the form asks "
                   "(overbook_label, overbook_reason)")
    for k in ("overbook_priority",):
        if b.get(k) and "priority" not in rt.base_map:
            out.append(f"booking.{k}: the type keeps no priority (base field priority)")
    if b.get("overbook") and "overbooked" not in rt.base_map:
        out.append("booking.overbook: the type keeps no reason for booking over another (base field overbooked)")
    if b.get("request_inputs") and not b.get("request"):
        out.append("booking.request_inputs: name the workflow (request) they are inputs of")
    pf = rt.field(((rt.base_map.get("priority") or {}).get("fields") or [""])[0])
    if pf and pf.get("choices"):
        for v in [*(b.get("marked") or []), *([b["overbook_priority"]] if b.get("overbook_priority") else [])]:
            if v not in pf["choices"]:
                out.append(f"booking: {v!r} is not one of {pf['name']}'s choices ({', '.join(pf['choices'])})")
    if not out:
        rt.booking = {"marked": list(b.get("marked") or []), "overbook": list(b.get("overbook") or []),
                      "overbook_label": str(b.get("overbook_label") or ""),
                      "overbook_reason": str(b.get("overbook_reason") or ""),
                      "overbook_priority": str(b.get("overbook_priority") or ""),
                      "request": str(b.get("request") or ""), "request_inputs": list(b.get("request_inputs") or [])}
    return out


LASTS = re.compile(r"(\d{1,4})([dwmy])")


def _check_consent(rt: RecordType, c) -> list[str]:
    """``[consent]``: which fields hold a record's consent to commercial messages. Sets ``rt.consent`` when sound."""
    if not isinstance(c, dict):
        return ["consent: a table naming its fields (kind, date, source, withdrawn) and lasts"]
    out = _unknown("consent", c, "consent")
    want = {"kind": ("choice",), "date": ("date", "datetime"), "source": ("string", "text", "choice"),
            "withdrawn": ("date", "datetime")}
    paths = {}
    for k, types in want.items():
        f = rt.field(str(c.get(k) or ""))
        if not f or f["type"] not in types or f.get("list") or "*" in f["path"]:
            out.append(f"consent.{k}: name its {'/'.join(types)} field ({c.get(k)!r} is not one)")
        else:
            paths[k] = f["path"]
    lasts = c.get("lasts")
    if not isinstance(lasts, dict) or not lasts:
        out.append('consent.lasts: a table of the kinds that count and how long each lasts, e.g. '
                   '{ express = "", implied = "2y" }')
        lasts = {}
    kf = rt.field(str(c.get("kind") or ""))
    for kind, how in lasts.items():
        if kf and kind not in (kf.get("choices") or []):
            out.append(f"consent.lasts: {kind!r} is not one of {kf['name']}'s choices")
        if not isinstance(how, str) or (how and not LASTS.fullmatch(how)):
            out.append(f"consent.lasts.{kind}: how long it lasts, like \"2y\" or \"6m\" (\"\" for until withdrawn)")
    if not out:
        rt.consent = {**paths, "lasts": dict(lasts)}
    return out


def _inside(path: str, outer: str) -> bool:
    """Whether ``path`` lies within ``outer`` (``reasonCode.0.text`` within ``reasonCode``; ``*`` matches a position)."""
    a, b = path.split("."), outer.split(".")
    return len(a) > len(b) and all(x == y or "*" in (x, y) or (x.isdigit() and y.isdigit() and x == y)
                                   for x, y in zip(a, b))


def _check_field_classes(rt: RecordType) -> list[str]:
    """A field's own data class must make sense in the record: what says which record it is (its state, its owner,
    its parent, its base fields' subject) stays with the type's class, and a field inside another holds no less
    guarded data than the one around it (a reader would see it there)."""
    problems = []
    keep = {(rt.lifecycle or {}).get("path"), (rt.part_of or {}).get("path"), rt.parent}
    subj = (rt.base_map.get("subject") or {}).get("fields") or []
    for f in rt.classed_fields:
        if f["path"] in keep or f["name"] in subj or (f["rel"] == "subject" and f["type"] == "reference"):
            problems.append(f"field {f['name']}: it says which record this is (its state, owner, parent or "
                            f"subject): it keeps the type's data class")
    for f in rt.fields:
        for g in rt.fields:
            if f is not g and _inside(f["path"], g["path"]) and g.get("data_class") \
                    and rt.field_class(f) != rt.field_class(g):
                problems.append(f"field {f['name']}: it is inside {g['name']}, which is {rt.field_class(g)}: give it "
                                f"the same data class")
    return problems


STANDARDS = {"fhir-r4": "FHIR R4"}   # what ``standard`` may say: the standard its records are resources of
FHIR_NAME = re.compile(r"[A-Z][A-Za-z]+")       # a FHIR resource type: letters, capital first
FHIR_ELEMENT = re.compile(r"[a-z][A-Za-z0-9]*")  # a FHIR element: camelCase, small first


def _check_standard(rt: RecordType, std) -> list[str]:
    """A type that says its records are FHIR resources must be able to be one: a resource name, and no keys beyond
    its fields (strict). A field whose key is not a FHIR element name is exported as an extension."""
    if std not in STANDARDS:
        return [f"standard: {std!r} is not a standard the hub exports ({', '.join(sorted(STANDARDS))})"]
    out = []
    if not FHIR_NAME.fullmatch(rt.id):
        out.append(f"standard: {rt.id!r} is not a FHIR resource name (letters only, a capital first)")
    if not rt.strict:
        out.append("standard: a FHIR resource holds only FHIR's elements; say strict = true")
    return out  # a field of the practice's own (plan_code) goes out as a FHIR extension


def _screens(doc: dict, fields: list[dict], base_map: dict, lc: dict | None, problems: list) -> dict:
    """``[[screen]]``: which fields a form shows, in what tabs and order, for an operation (``on = ["create"]``) or a
    lifecycle move (``screen = "<id>"`` on the transition). The field's own settings (required, help, choices) stay
    with the field. Two screens of the same id are one: a module's ``extends/types/<Type>.toml`` adds a tab to the
    practice's screen that way. A screen may name a base field (``subject``) for the field that holds it."""
    byname = {f["name"]: f for f in fields}

    def resolve(n):
        if n in byname:
            return n
        m = base_map.get(n) or {}
        return m["fields"][0] if len(m.get("fields", [])) == 1 else None
    out: dict = {}
    for s in _tables(doc, "screen", problems, "screen"):
        sid = str(s.get("id") or "")
        problems += _unknown(f"screen {sid or len(out) + 1}", s, "screen")
        if not SCREEN_ID.fullmatch(sid):
            problems.append(f"screen: {sid!r} is not a screen id (lower case letters, digits and -)")
            continue
        sc = out.setdefault(sid, {"id": sid, "on": [], "tabs": []})
        on = s.get("on", [sid] if sid in OPS else [])
        on = [on] if isinstance(on, str) else on
        if not _strings(on):
            problems.append(f"screen {sid}: on is a list of {', '.join(OPS)}")
            on = []
        for op in on:
            if op not in OPS:
                problems.append(f"screen {sid}: on is one of {', '.join(OPS)}, not {op!r}")
            elif op not in sc["on"]:
                sc["on"].append(op)
        tabs = _tables(s, "tab", problems, f"screen {sid} tab")
        if "fields" in s:
            tabs.insert(0, {"label": s.get("label", ""), "fields": s["fields"]})
        for t in tabs:
            label, names = str(t.get("label") or ""), []
            problems += _unknown(f"screen {sid} tab {label!r}", t, "tab")
            tf = t.get("fields", []) or []
            if not _strings(tf):
                problems.append(f"screen {sid} tab {label!r}: fields is a list of field names")
                tf = []
            for n in tf:
                r = resolve(str(n))
                if r is None:
                    problems.append(f"screen {sid}: there is no field {n!r}")
                elif r not in names:
                    names.append(r)
            tab = next((x for x in sc["tabs"] if x["label"] == label), None)
            if tab is not None:
                tab["fields"] += [n for n in names if n not in tab["fields"]]
            else:
                sc["tabs"].append({"label": label, "fields": names})
    for op in OPS:
        owners = [s["id"] for s in out.values() if op in s["on"]]
        if len(owners) > 1:
            problems.append(f"screen: {' and '.join(owners)} are both shown to {op}; one screen per operation")
    create = next((s for s in out.values() if "create" in s["on"]), None)
    if create is not None:
        on_it = {n for t in create["tabs"] for n in t["fields"]}
        paths = [byname[n]["path"] for n in on_it]
        state = lc["field"] if lc else None
        for f in fields:  # a required field missing from the create screen would make every new record fail
            inside = any(q.startswith(f["path"] + ".") for q in paths)  # given and family fill the name
            if f["required"] and f["name"] not in on_it and f["name"] != state and not inside:
                problems.append(f"screen {create['id']}: {f['name']} is required, so the create screen shows it")
    for t in (lc or {}).get("transition", []):
        if t["screen"] is not None:
            sc = out.get(str(t["screen"]))
            if sc is None:
                problems.append(f"lifecycle.transition to {t['to']!r}: there is no screen {t['screen']!r}")
            else:
                t["asks"] = [n for tab in sc["tabs"] for n in tab["fields"]] + t["asks"]
        def ok(names):
            out = []
            for n in names:
                r = resolve(str(n))
                if r is None:
                    problems.append(f"lifecycle.transition to {t['to']!r}: there is no field {n!r}")
                elif r == lc["field"]:
                    problems.append(f"lifecycle.transition to {t['to']!r}: the move itself sets {r}")
                elif r not in out:
                    out.append(r)
            return out
        t["requires"] = ok(t["requires"])
        if t.get("opens") and resolve(t["opens"]["field"]) is None:
            problems.append(f"lifecycle.transition to {t['to']!r}: opens names no field {t['opens']['field']!r}")
        # what a move requires, its screen asks for
        asked = ok(t["asks"])
        t["asks"] = asked + [n for n in t["requires"] if n not in asked]
        t["comment"] = bool(t["comment"])
        if t.get("stamp"):
            t["stamp"] = _stamp_fields(t, ok(t["stamp"]), byname, problems)
    return out


STAMPS = ("member", "date", "datetime")


def _stamp_fields(t: dict, names: list[str], byname: dict, problems: list) -> list[str]:
    """``stamp``: fields the move itself sets as it is made, in its own write (so a signature that makes it covers
    them): a member field to who made it (for a workflow's move, whoever did the step), a date to today, a time to
    now. Nothing else writes them, so a reviewer's name is the name of whoever made the move. Each is marked
    ``by = "workflow"``: off every form and every tool's input."""
    out = []
    where = f"lifecycle.transition to {t['to']!r}: stamp"
    for n in names:
        f = byname.get(n)
        if f is None:
            continue
        if f["type"] not in STAMPS or f.get("list") or "*" in f["path"]:
            problems.append(f"{where}: {n} is {'a list' if f.get('list') else f['type']}; a stamp is one "
                            f"{', '.join(STAMPS[:-1])} or {STAMPS[-1]} field")
        elif n in t["asks"]:
            problems.append(f"{where}: {n} is asked for by the move as well; the move sets it, so it is not asked")
        else:
            f["by"] = "workflow"
            out.append(n)
    return out


def stamped(rt: "RecordType") -> set[str]:
    """The fields the type's moves stamp (``stamp``): only a move writes them."""
    return {n for t in (rt.lifecycle or {}).get("transition", []) for n in t.get("stamp", [])}


# ---------------------------------------------------------------------------- a move's time and a state's tone
TONES = ("waiting", "active", "attention", "done", "ended")  # a lifecycle's states, as a page marks them
OPENS = re.compile(r"\s*([A-Za-z_][\w.]*)\s*(?:([+-])\s*(\d{1,4})\s*([mhd]))?\s*")


def _transition_opens(value, where: str, problems: list) -> dict | None:
    """``opens``: a move a person may make only from a time the record keeps, moved on or back by minutes, hours or
    days (``"plannedStartDate - 2h"``: from two hours before it is booked for). As ``{field, minutes}``."""
    if value is None:
        return None
    m = OPENS.fullmatch(value) if isinstance(value, str) else None
    if not m:
        problems.append(f"{where}: opens is a time field, moved by minutes, hours or days (plannedStartDate - 2h)")
        return None
    n = int(m.group(3) or 0) * {"m": 1, "h": 60, "d": 1440}[m.group(4) or "m"]
    return {"field": m.group(1), "minutes": -n if m.group(2) == "-" else n}


def opens_at(rt: "RecordType", t: dict, rec: dict):
    """When a move with ``opens`` opens for this record, as the practice's wall time (None: it does not wait for a
    time, or the record keeps no such time)."""
    from datetime import datetime, timedelta
    o = t.get("opens")
    f = rt.field(o["field"]) if o else None
    v = first(rec, f["path"]) if f else None
    try:
        at = datetime.fromisoformat(str(v)[:19]) if v else None
    except ValueError:
        return None
    return at + timedelta(minutes=o["minutes"]) if at else None


def _lifecycle_tones(value, states: list, problems: list) -> dict:
    """``tones``: how each state reads at a glance, declared by the type rather than guessed from its words: waiting
    (nobody has to act yet), active (work under way), attention (someone must act), done, ended (stopped, undone)."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
        problems.append("lifecycle.tones: a table of state = " + " | ".join(TONES))
        return {}
    for st, tone in value.items():
        if st not in states:
            problems.append(f"lifecycle.tones: {st!r} is not one of its states")
        if tone not in TONES:
            problems.append(f"lifecycle.tones: {st} is {tone!r}, not one of {', '.join(TONES)}")
    return {st: tone for st, tone in value.items() if st in states and tone in TONES}


def _fits(bf: dict, f: dict) -> bool:
    """Whether a pack's field can hold a base field's value (its contract type)."""
    bt, t = bf["type"], f["type"]
    if bt in ("string", "text"):
        return t not in ("attachment", "json")
    if bt in ("date", "datetime"):
        return t in ("date", "datetime")
    if bt == "member":
        return t in ("member", "reference")
    return bt == t


def _inherit(base: RecordType, fields: list[dict], spec: dict) -> tuple[list[dict], dict, list[str]]:
    """A pack type's fields with the base's merged in, and where each base field is held."""
    problems, out, bmap = [], list(fields), {}
    by_name = {f["name"]: f for f in fields}
    for k in spec:
        if not base.field(k):
            near = difflib.get_close_matches(str(k), [f["name"] for f in base.fields], n=1)
            problems.append(f"base.{k}: {base.id} has no field {k!r} (" + (f"did you mean {near[0]!r}? " if near else "")
                            + f"its fields: {', '.join(f['name'] for f in base.fields)})")
    for bf in base.fields:
        name, m = bf["name"], spec.get(bf["name"])
        where = f"base.{name}"
        if m is False:
            if bf["required"]:
                problems.append(f"{where}: {base.id} needs it; name the field that holds it")
            continue
        if isinstance(m, dict):
            if set(m) != {"when"} or bf["type"] != "boolean":
                problems.append(f"{where}: {{ when = ... }} is for a true/false base field: a condition over record")
                continue
            try:
                expr.check(str(m["when"]))
            except expr.ExprError as exc:
                problems.append(f"{where}: {exc}")
                continue
            bmap[name] = {"when": str(m["when"])}
            continue
        if m is None and name not in by_name:
            if any(f["path"].split(".")[0] == bf["path"].split(".")[0] for f in out):
                problems.append(f"{where}: the type keeps something else at {bf['path']!r}; name the field that "
                                f"holds {base.id}'s {name}, or say {name} = false")
                continue
            out.append(dict(bf))
            bmap[name] = {"fields": [name]}
            continue
        names = [name] if m is None else [m] if isinstance(m, str) else list(m) if isinstance(m, list) else None
        if not names or not all(isinstance(n, str) for n in names):
            problems.append(f"{where}: a field name, a list of them, {{ when = ... }} or false")
            continue
        bad = [n for n in names if n not in by_name]
        if bad:
            problems.append(f"{where}: there is no field {bad[0]!r}")
            continue
        if len(names) > 1 and bf["type"] not in ("string", "text"):
            problems.append(f"{where}: only a text base field is made of several fields")
            continue
        for n in names:
            if not _fits(bf, by_name[n]):
                problems.append(f"{where}: field {n} is {by_name[n]['type']}; {base.id}'s {name} is {bf['type']}")
        bmap[name] = {"fields": names}
    return out, bmap, problems


MAX_CHAIN = 8  # the most types a chain runs through, the core base at its root aside
# What a type under another says of its own: each of these is its parent's, and it may not say it otherwise.
FIXED_KEYS = ("data_class", "base", "lifecycle", "workflow", "number", "reverses", "covers", "period", "closing",
              "part_of", "parent", "consent", "booking", "standard", "strict", "subject", "active", "date", "flag")
NEVER_INHERITED = ("id", "label", "plural", "slug", "abstract")
ADDED_KEYS = ("field", "check", "signature", "link", "unique", "screen")  # the parent's, then its own
# A field it narrows keeps these as its parent's field has them.
FIELD_STAYS = ("type", "path", "list", "data_class", "by", "pin", "rel", "with", "live", "scale", "unit",
               "required_unless", "columns", "from_file", "graph")


def parse_all(sources: dict[str, str]) -> dict[str, RecordType]:
    """Record types read together without a hub (a pack's, from its files), by id: one under another of them is
    read under it. A DefinitionError names the type it is about."""
    docs: dict[str, tuple[str, object]] = {}
    for name, src in sources.items():
        try:
            doc = tomllib.loads(src)
        except tomllib.TOMLDecodeError as exc:
            raise DefinitionError([f"{name}: not valid TOML: {exc}"]) from None
        docs[str(doc.get("id", name))] = (src, doc.get("extends"))
    out: dict[str, RecordType] = {}

    def one(tid: str, under: tuple) -> RecordType:
        if tid not in out:
            src, ext = docs[tid]
            if tid in under:
                raise DefinitionError([f"{tid}: extends: it would be under itself ({' → '.join([*under, tid])})"])
            parent = one(ext, (*under, tid)) if isinstance(ext, str) and ext in docs and ext not in bases() else None
            try:
                out[tid] = parse(src, parent=parent)
            except DefinitionError as exc:
                raise DefinitionError([f"{tid}: {p}" for p in exc.problems]) from None
        return out[tid]
    for tid in docs:
        one(tid, ())
    return out


def _merge(parent: RecordType, own: dict) -> tuple[dict, list[str]]:
    """A type's definition under ``parent``: its parent's (as merged with its own parents'), with what it says itself
    merged in, and what it says that would change its parent's. ``extends`` becomes the core base at the root, if
    any, which ``_inherit`` reads the base fields of."""
    pdoc, problems = parent.doc, []
    out = {k: v for k, v in pdoc.items() if k not in NEVER_INHERITED and k not in ADDED_KEYS}
    out["key"] = parent.key  # shared, unless it sets its own
    for k, v in own.items():
        if k in FIXED_KEYS:
            if v != pdoc.get(k) and not (k in ("subject", "strict") and v == getattr(parent, k)):
                problems.append(f"{k} is {parent.id}'s: a type under it adds to it, never changes it")
        elif k not in ADDED_KEYS and k != "extends":
            out[k] = v
    for k in ADDED_KEYS:
        mine, theirs = own.get(k), pdoc.get(k)
        if mine is None or theirs is None or not isinstance(mine, list) or not isinstance(theirs, list):
            if mine is not None or theirs is not None:
                out[k] = mine if mine is not None else theirs  # a shape of its own is said by the check of it
            continue
        merged = [dict(x) if isinstance(x, dict) else x for x in theirs]
        name = {"field": "name", "signature": "meaning", "link": "rel", "screen": "id"}.get(k)
        at = {x.get(name): i for i, x in enumerate(merged) if name and isinstance(x, dict)}
        for x in mine:
            i = at.get(x.get(name)) if name and isinstance(x, dict) else None
            if k == "unique" and x in merged:
                continue
            if i is None:
                merged.append(x)
            elif k == "field":  # a field it narrows: its parent's, with what it changes
                old, new = merged[i], {**merged[i], **x}
                if "choices" in x and "labels" not in x and isinstance(old.get("labels"), dict) \
                        and isinstance(x["choices"], list):
                    new["labels"] = {c: w for c, w in old["labels"].items() if c in x["choices"]}
                merged[i] = new
            elif k == "screen":  # a screen of the same id is its own
                merged[i] = x
            else:
                what = {"signature": "signatures", "link": "links"}[k]
                problems.append(f"{k} {x.get(name)!r} is {parent.id}'s: a type under it adds {what}, never changes one")
        out[k] = merged
    added = [x["name"] for x in own.get("field") or [] if isinstance(x, dict) and isinstance(x.get("name"), str)
             and parent.field(x["name"]) is None and not x.get("advanced")]
    if added and isinstance(out.get("screen"), list):  # a screen it takes from its parent shows its own fields too
        mine = {x.get("id") for x in own.get("screen") or [] if isinstance(x, dict)}
        tab = {"label": str(own.get("label") or default_label(str(own.get("id") or ""))), "fields": added}
        out["screen"] = [{**sc, "tab": [*sc.get("tab", []), tab]}
                         if isinstance(sc, dict) and sc.get("id") not in mine and isinstance(sc.get("tab", []), list)
                         else sc for sc in out["screen"]]
    return out, problems


def _narrowed(parent: RecordType, own: dict, fields: list[dict], doc: dict, catalog) -> list[str]:
    """What a type under ``parent`` says of the fields it names again that would widen or change them: it may only
    narrow one (``_narrows``), and only a field one of its parents wrote (a base type's field stays the base's)."""
    raw = own.get("field") if isinstance(own.get("field"), list) else []
    written = {x.get("name") for x in parent.doc.get("field") or [] if isinstance(x, dict)}
    lc = doc.get("lifecycle")
    lf = lc.get("field", "status") if isinstance(lc, dict) else None
    out = []
    for x in raw:
        n = x.get("name") if isinstance(x, dict) else None
        pf, f = parent.field(n) if n else None, next((f for f in fields if f["name"] == n), None)
        if pf is None:
            continue
        if n not in written:
            out.append(f"field {n}: it is {parent.base}'s, as {parent.id} has it: a type under it does not change it")
        elif f is not None:
            out += _narrows(pf, f, parent.id, lf, catalog)
    return out


def _narrows(pf: dict, f: dict, who: str, lf: str | None, catalog) -> list[str]:
    """What field ``f`` changes of ``pf``, its parent's field of the same name, beyond narrowing it: required, fewer
    choices or roles, tighter bounds, a target under the parent's, and its own label and help."""
    where = f"field {f['name']}"
    out = [f"{where}: its {k} is {who}'s: a type under it narrows a field, never changes it"
           for k in FIELD_STAYS if pf.get(k) != f.get(k)]
    if pf["required"] and not f["required"]:
        out.append(f"{where}: {who} requires it, and so does every type under it")
    if f["choices"] != pf["choices"]:
        if f["name"] == lf:
            out.append(f"{where}: its choices are the states of {who}'s lifecycle: a type under it keeps them")
        elif not set(f["choices"]) <= set(pf["choices"]):
            out.append(f"{where}: a type under {who} gives fewer of its choices, never others "
                       f"({', '.join(repr(c) for c in f['choices'] if c not in pf['choices'])})")
    for k, tighter in (("min", True), ("above", True), ("max", False), ("max_length", False), ("min_rows", True),
                       ("max_rows", False)):
        old, new = pf.get(k), f.get(k)
        if old is not None and (new is None or (new < old if tighter else new > old)):
            out.append(f"{where}: {k} is {old} at {who}: a type under it keeps it or makes it tighter")
    if pf["roles"] and not (f["roles"] and set(f["roles"]) <= set(pf["roles"])):
        out.append(f"{where}: {who} lets members of {', '.join(pf['roles'])} be picked: a type under it lets some "
                   f"of them")
    if f["target"] != pf["target"] and catalog is not None:
        wider = [t for t in f["target"]
                 if not any(t == o or o in ("*", ROOT) or catalog.is_a(t, o) for o in pf["target"])]
        if wider:
            out.append(f"{where}: {who}'s refers to a {' or '.join(pf['target'])}: a type under it refers to the "
                       f"types under that, and {', '.join(wider)} is not one")
    return out


_BASES: dict | None = None


def bases() -> dict[str, RecordType]:
    """The core's base types, by id."""
    global _BASES
    if _BASES is None:
        out = {}
        for p in sorted(BASE_DIR.glob("*.toml")):
            rt = parse(p.read_text(), base_ok=True)
            out[rt.id] = rt
        _BASES = out
    return _BASES


def choice_words(f: dict | None, v) -> str:
    """A field's value in words: a choice's label when the field gives one (AMB: "In clinic"), else the value."""
    if v is None:
        return ""
    return str(((f or {}).get("labels") or {}).get(str(v)) or v)


def state_words(st) -> str:
    """A state's code as words, where it has no label: in-progress, entered_in_error -> In progress, ..."""
    text = str(st).replace("-", " ").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text and not text.isupper() else text


def words(v) -> str:
    """A value as plain words: what a base text field holds, whatever shape the pack keeps it in."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (str, int, float)):
        return str(v)
    if isinstance(v, list):
        return " ".join(w for w in (words(x) for x in v) if w)
    if isinstance(v, dict):
        for k in ("text", "display", "value", "name"):
            if isinstance(v.get(k), (str, int, float)) and str(v[k]).strip():
                return str(v[k])
        return " ".join(w for k, x in v.items() if k not in ("system", "use", "id", "extension") for w in [words(x)] if w)
    return str(v)


def _contract(bf: dict, f: dict, item, member_types) -> object:
    bt = bf["type"]
    if bt in ("string", "text"):
        return words(item) or None
    if bt == "member":
        if f["type"] == "member":
            return str(item).removeprefix("member:") if item else None
        ref = ref_of(item)
        return ref.split("/", 1)[1] if ref and ref.split("/", 1)[0] in member_types else None
    if bt == "reference":
        return ref_of(item)
    return item


def project(rt: RecordType | None, rec: dict, member_types=()) -> dict:
    """The record as its base type sees it: ``{base field: value}`` in the base's own types (text is words, a member
    is a member id, a reference is ``Type/id``). The core and modules read a pack's records only this way."""
    base = bases().get(rt.base) if rt is not None and rt.base else None
    if base is None:
        return {}
    out = {}
    for bf in base.fields:
        m = rt.base_map.get(bf["name"])
        if m is None:
            continue
        if "when" in m:
            try:
                out[bf["name"]] = expr.holds(m["when"], {"record": rec})
            except expr.ExprError:
                out[bf["name"]] = False
            continue
        vals = []
        for n in m["fields"]:
            f = rt.field(n)
            for v in get(rec, f["path"]):
                for item in (v if isinstance(v, list) else [v]):
                    c = _contract(bf, f, item, member_types)
                    if c not in (None, ""):
                        vals.append(c)
        if bf["list"]:
            out[bf["name"]] = vals
        elif bf["type"] in ("string", "text") and len(m["fields"]) > 1:
            out[bf["name"]] = " ".join(map(str, vals)) or None
        else:
            out[bf["name"]] = vals[0] if vals else None
    return out


def compose(rt: RecordType, values: dict, member_ref: str, rec: dict | None = None) -> dict:
    """A record of ``rt`` from base-field values (the inverse of ``project``), on top of ``rec``. A member becomes a
    reference to ``member_ref``/id where the pack keeps references; a ``*`` path or a list adds an item."""
    base = bases().get(rt.base or "")
    if base is None:
        raise ValueError(f"{rt.label} extends no base type")
    rec = dict(rec or {})
    for bf in base.fields:
        val = values.get(bf["name"])
        if val in (None, "", []):
            continue
        m = rt.base_map.get(bf["name"]) or {}
        if len(m.get("fields") or []) != 1:
            raise ValueError(f"{rt.label} keeps {base.id}'s {bf['name']} in no one field it can be written to")
        f = rt.field(m["fields"][0])
        for item in (val if bf["list"] and isinstance(val, list) else [val]):
            if f["type"] == "reference":
                ref = item if "/" in str(item) else f"{member_ref}/{item}"
                item = {"reference": str(ref)}
            rec = _place(rec, f, item)
    return rec


def place(rec: dict, f: dict, value) -> dict:
    """A copy of ``rec`` with ``value`` written where field ``f`` keeps it (with its siblings; a list field adds it)."""
    return _place(rec, f, value)


def _place(rec: dict, f: dict, value) -> dict:
    parts = f["path"].split(".")
    if "*" in parts:
        i = parts.index("*")
        lst = first(rec, ".".join(parts[:i]))
        new = set_in({}, parts[i + 1:], value, f["with"]) if parts[i + 1:] else value
        return set_in(rec, parts[:i], (lst if isinstance(lst, list) else []) + [new])
    if f.get("list"):
        cur = first(rec, f["path"])
        return set_in(rec, parts, (cur if isinstance(cur, list) else []) + [value], f["with"])
    return set_in(rec, locate(rec, f["path"], f["with"]).split("."), value, f["with"])


TITLE_PART = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _check_title(t: str) -> list[str]:
    depth = 0
    for ch in t:
        depth += {"[": 1, "]": -1}.get(ch, 0)
        if depth not in (0, 1):
            return ["optional parts [...] do not nest and close what they open"]
    return [] if depth == 0 else ["an optional part [... is not closed"]


def render_title(template: str, values: dict) -> str:
    """``{name}`` is a value's words; ``[ ... ]`` is left out when a value in it is empty."""
    def fill(seg: str, optional: bool) -> str:
        empty = False

        def one(m):
            nonlocal empty
            w = words(values.get(m.group(1)))
            if not w:
                empty = True
            return w
        out = TITLE_PART.sub(one, seg)
        return "" if optional and empty else out
    out, i = [], 0
    for m in re.finditer(r"\[([^\]]*)\]", template):
        out.append(fill(template[i:m.start()], False))
        out.append(fill(m.group(1), True))
        i = m.end()
    out.append(fill(template[i:], False))
    return "".join(out).strip()


def permissive(tid: str, data_class: str, label: str | None = None) -> RecordType:
    """A type with no declared fields, for a pack that names a record type but ships no schema for it."""
    src = f'id = "{tid}"\nlabel = "{label or tid}"\ndata_class = "{data_class}"\nstrict = false\n'
    return parse(src)


# --------------------------------------------------------------------------------------------------- paths
def get(rec, path: str):
    """The values at ``path`` (a list: ``*`` may match several)."""
    cur = [rec]
    for part in path.split("."):
        nxt = []
        for c in cur:
            if part == "*":
                if isinstance(c, list):
                    nxt += c
            elif isinstance(c, dict):
                if part in c:
                    nxt.append(c[part])
            elif isinstance(c, list) and part.isdigit():
                if int(part) < len(c):
                    nxt.append(c[int(part)])
        cur = nxt
    return cur


def first(rec, path: str | None):
    if not path:
        return None
    vals = get(rec, path)
    return vals[0] if vals else None


def locate(rec, path: str, siblings: dict | None = None) -> str:
    """Where a field's value is in this record. A list position beside ``with`` siblings (``telecom.0.value`` with
    ``system = phone``) means the entry that has those siblings, wherever it sits: the phone is the phone even when
    an email came first. With no such entry, the stated position if nothing else claims it, else a new entry."""
    parts = path.split(".")
    pos = next((i for i in range(len(parts) - 1, -1, -1) if parts[i].isdigit()), None)  # the innermost list
    if not siblings or pos is None:
        return path
    lst = rec
    for p in parts[:pos]:
        if p.isdigit():
            lst = lst[int(p)] if isinstance(lst, list) and int(p) < len(lst) else None
        else:
            lst = lst.get(p) if isinstance(lst, dict) else None
    if not isinstance(lst, list):
        return path
    for i, entry in enumerate(lst):
        if isinstance(entry, dict) and all(entry.get(k) == v for k, v in siblings.items()):
            return ".".join([*parts[:pos], str(i), *parts[pos + 1:]])
    i = int(parts[pos])
    entry = lst[i] if i < len(lst) else None
    if i < len(lst) and not (isinstance(entry, dict) and any(k in entry for k in siblings)):
        return path
    return ".".join([*parts[:pos], str(len(lst)), *parts[pos + 1:]])


def put(rec: dict, path: str, value) -> dict:
    """A copy of ``rec`` with ``value`` at a dotted path of plain keys (for a lifecycle's initial state)."""
    out = dict(rec)
    cur = out
    parts = path.split(".")
    for part in parts[:-1]:
        nxt = dict(cur.get(part) or {}) if isinstance(cur.get(part), dict) or cur.get(part) is None else None
        if nxt is None:
            raise ValueError(f"{path}: {part} is not an object")
        cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return out


def set_in(value, parts: list[str], new, siblings: dict | None = None):
    """A copy of ``value`` with ``new`` at the path ``parts`` (keys and list positions), making the objects and
    lists on the way. ``siblings`` are set beside the new value where absent (a new phone gets ``system = phone``).
    A ``new`` of None removes the key."""
    import copy
    if not parts:
        return new
    head, rest = parts[0], parts[1:]
    if head.isdigit():
        out = copy.deepcopy(value) if isinstance(value, list) else []
        i = int(head)
        if new is None and i >= len(out):
            return out
        while len(out) <= i:
            out.append({} if rest and not rest[0].isdigit() else [] if rest else None)
        out[i] = set_in(out[i], rest, new, siblings)
        return out
    out = copy.deepcopy(value) if isinstance(value, dict) else {}
    if not rest:
        if new is None:
            out.pop(head, None)
        else:
            out[head] = new
            for k, v in (siblings or {}).items():
                out.setdefault(k, v)
        return out
    out[head] = set_in(out.get(head), rest, new, siblings)
    return out


REF_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9_]*/[A-Za-z0-9_.-]+")


def ref_of(v) -> str | None:
    if isinstance(v, dict):
        v = v.get("reference")
    return v if isinstance(v, str) and "/" in v else None


# ----------------------------------------------------------------------------------------------- validation
def _check_value(f: dict, v, members=None, is_a=None) -> str | None:
    t, label = f["type"], f["label"]
    if t in ("string", "text", "markdown", "member"):
        if not isinstance(v, str):
            return f"{label}: expected text, got {type(v).__name__}"
        limit = f.get("max_length") or {"text": 20000, "markdown": 200000}.get(t, 2000)
        if len(v) > limit:
            return f"{label} is longer than {limit} characters"
        if t == "member" and members is not None and v.removeprefix("member:") not in members:
            return f"{label}: there is no member {v!r}"
    elif t == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return f"{label}: expected a number"
        if f.get("min") is not None and v < f["min"]:
            return f"{label} is below {f['min']}"
        if f.get("above") is not None and v <= f["above"]:
            return f"{label} must be more than {f['above']:g}"
        if f.get("max") is not None and v > f["max"]:
            return f"{label} is above {f['max']}"
    elif t == "decimal":
        if not isinstance(v, decimals.Decimal) and not isinstance(v, (dict, list)):  # ``exact`` left it: say why
            try:
                decimals.to_decimal(v, f["scale"])
            except ValueError as exc:
                return f"{label}: {exc}"
        if not isinstance(v, decimals.Decimal) or not v.is_finite() or v.as_tuple().exponent != -f["scale"]:
            return f"{label}: expected a number like {decimals.example(f['scale'])}"
        if len(v.as_tuple().digits) > decimals.DIGITS and not v.is_zero():
            return f"{label}: at most {decimals.DIGITS} digits"
        if f.get("min") is not None and v < f["min"]:
            return f"{label} is below {decimals.text(f['min'])}"
        if f.get("above") is not None and v <= f["above"]:
            return f"{label} must be more than {decimals.text(f['above'])}"
        if f.get("max") is not None and v > f["max"]:
            return f"{label} is above {decimals.text(f['max'])}"
    elif t == "table":
        return _check_table(f, v, members, is_a)
    elif t == "boolean":
        if not isinstance(v, bool):
            return f"{label}: expected true or false"
    elif t == "date":
        try:
            if not isinstance(v, str) or not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", v):
                raise ValueError
            if len(v) == 10:
                date.fromisoformat(v)
        except ValueError:
            return f"{label}: {str(v)[:30]!r} is not a date (YYYY-MM-DD)"
    elif t == "datetime":
        try:
            if not isinstance(v, str):
                raise ValueError
            datetime.fromisoformat(v.replace("Z", "+00:00")) if "T" in v else date.fromisoformat(v)
        except ValueError:
            return f"{label}: {str(v)[:30]!r} is not a date and time"
    elif t == "choice":
        if str(v) not in [str(c) for c in f["choices"]]:
            return f"{label}: {str(v)[:30]!r} is not one of {', '.join(map(str, f['choices']))}"
    elif t == "reference":
        ref = ref_of(v)
        if not ref:
            return f"{label}: expected a reference like {f['target'][0]}/<id>"
        t = ref.split("/")[0]
        if f["target"] and "*" not in f["target"] and t not in f["target"] and not (
                is_a and any(is_a(t, x) for x in f["target"])):
            return f"{label}: {ref} is not a {' or '.join(f['target'])}"
    elif t == "attachment":
        if not isinstance(v, dict) or not (v.get("blob") or v.get("url") or v.get("data")):
            return f"{label}: an attachment is {{blob: <sha256>, name, contentType}} or {{url}}"
    elif t == "object":
        if not isinstance(v, dict):
            return f"{label}: expected an object"
    elif t == "list":
        if not isinstance(v, list):
            return f"{label}: expected a list"
    return None


def _check_table(f: dict, rows, members=None, is_a=None) -> str | None:
    """A table's rows: a list of rows, each holding only its columns, each column's value checked as a field's is."""
    label = f["label"]
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return f"{label}: expected rows (a list of {{column: value}})"
    if f.get("min_rows") and len(rows) < f["min_rows"]:
        return f"{label}: at least {f['min_rows']} row{'s' if f['min_rows'] != 1 else ''}"
    if len(rows) > (f.get("max_rows") or MAX_ROWS):
        return f"{label}: at most {f.get('max_rows') or MAX_ROWS} rows"
    names = {c["name"] for c in f["columns"]}
    for i, row in enumerate(rows, 1):
        extra = sorted(k for k in row if k not in names)
        if extra:
            return f"{label}, row {i}: {', '.join(extra)} {'is not a column' if len(extra) == 1 else 'are not columns'}"
        for c in f["columns"]:
            v = row.get(c["name"])
            if v in (None, ""):
                if c.get("required"):
                    return f"{label}, row {i}: {c['label']} is required"
                continue
            p = _check_value(c, v, members, is_a)
            if p:
                return f"{label}, row {i}: {p}"
    return None


def decimal_paths(rt: RecordType) -> list[dict]:
    """The decimal fields of a type, its tables' decimal columns among them (at their paths in the record)."""
    return [f for f in rt.fields + rt.cells() if f["type"] == "decimal"]


def exact(rt: RecordType | None, rec: dict) -> dict:
    """``rec`` with every decimal value exact and rounded half up to its field's places (12.345 at scale 2 is 12.35;
    "1,234.5" is 1234.50). A value that is not a number is left as it is, for ``validate`` to refuse. A record of a
    type with no decimals is returned as it is; else a copy."""
    fs = decimal_paths(rt) if rt else []
    if not fs or not isinstance(rec, dict):
        return rec
    import copy
    rec = copy.deepcopy(rec)

    def one(scale):
        def fix(v):
            if v is None or v == "" or isinstance(v, bool):
                return v
            try:
                return decimals.to_decimal(v, scale)
            except ValueError:
                return v
        return fix

    for f in fs:
        fix = one(f["scale"])
        _map_at(rec, f["path"].split("."), lambda v, fix=fix, many=f.get("list"):
                [fix(x) for x in v] if many and isinstance(v, list) else fix(v))
    return rec


def _map_at(obj, parts: list[str], fn) -> None:
    """Replace, in place, each value at a path (``*`` for every item of a list) with ``fn(value)``."""
    head, rest = parts[0], parts[1:]
    if head == "*":
        keys = range(len(obj)) if isinstance(obj, list) else []
    elif isinstance(obj, dict):
        keys = [head] if head in obj else []
    elif isinstance(obj, list) and head.isdigit():
        keys = [int(head)] if int(head) < len(obj) else []
    else:
        keys = []
    for k in keys:
        if rest:
            _map_at(obj[k], rest, fn)
        else:
            obj[k] = fn(obj[k])


def is_required(f: dict, rec: dict | None) -> bool:
    """Whether a field must be filled in on ``rec``: a required field is, except while its ``required_unless`` holds
    of the record (a booking asked for but not yet given a time has none)."""
    if not f.get("required"):
        return False
    unless = f.get("required_unless")
    return not (unless and rec is not None and expr.holds(unless, {"record": rec}))


def _field_problem(rt: RecordType, f: dict, rec: dict, partial_ok: bool, members, is_a) -> str | None:
    """The first problem with field ``f`` of ``rec``: missing when required, or a value its rules refuse."""
    vals = [v for v in get(rec, f["path"]) if v not in (None, "", [], {})]
    if not vals:
        return f"{rt.label}: {f['label']} is required" if is_required(f, rec) and not partial_ok else None
    for v in vals:
        for item in (v if (f.get("list") and isinstance(v, list)) else [v]):
            p = _check_value(f, item, members, is_a)
            if p:
                return f"{rt.label}: {p}"
    return None


def field_problems(rt: RecordType, rec: dict, names, *, members=None, is_a=None) -> list[str]:
    """What the rules of the fields ``names`` (a value's type, bounds, choices, target) refuse of their values in
    ``rec``; one left empty is not checked here (what must be filled is the write's own check)."""
    return [p for f in rt.fields if f["name"] in set(names)
            for p in [_field_problem(rt, f, rec, True, members, is_a)] if p]


def validate(rt: RecordType, rec: dict, *, partial_ok: bool = False, members=None, is_a=None) -> list[str]:
    """Every problem with ``rec`` as a record of type ``rt``. ``is_a(type, target)``: whether a type stands for a
    reference's target (a ``Subject`` target takes a Patient when Patient extends Subject)."""
    problems = []
    if rt.strict:
        extra = sorted(k for k in rec if k not in rt.top_keys())
        if extra:
            problems.append(f"{rt.label}: {', '.join(extra)} {'is not a field' if len(extra) == 1 else 'are not fields'}"
                            f" of this record type")
    for f in rt.fields:
        p = _field_problem(rt, f, rec, partial_ok, members, is_a)
        if p:
            problems.append(p)
    if rt.lifecycle:
        st = first(rec, rt.lifecycle["path"])
        if st is not None and st not in rt.lifecycle["states"]:
            problems.append(f"{rt.label}: {st!r} is not one of its states ({', '.join(rt.lifecycle['states'])})")
    return problems


LINK_REL = re.compile(r"[a-z][a-z0-9_-]*")


def _check_links(raw, rt: RecordType) -> tuple[list[dict], list[str]]:
    """``[[link]]``: the links a person may make from a record of this type to another record, by hand (a referral
    letter to its referral, a CAPA to the complaint that raised it): a rel, a label, the types it may point at, and
    whether more than one may be made. Its rel is never one of its reference fields' (those are made by the field)."""
    problems, out = [], []
    if not isinstance(raw, list) or not all(isinstance(x, dict) for x in raw):
        return [], ["link: a list of tables ([[link]])"]
    field_rels = {f["rel"] for f in rt.references}
    for i, x in enumerate(raw):
        rel = str(x.get("rel") or "")
        where = f"link {rel or i + 1!r}"
        problems += _unknown(where, x, "link")
        to = x.get("to")
        to = [to] if isinstance(to, str) else to
        if not LINK_REL.fullmatch(rel):
            problems.append(f"{where}: rel is lowercase letters, digits, dashes and underscores, starting with a letter")
        elif rel in field_rels:
            problems.append(f"{where}: the field that points with rel {rel!r} makes that link; name this one apart")
        elif rel == "mentions" or any(y["rel"] == rel for y in out):
            problems.append(f"{where}: rel {rel!r} is already a link of this type")
        if not to or not isinstance(to, list) or not all(isinstance(t, str) and t for t in to):
            problems.append(f"{where}: to is the record type (or list of them) it may point at")
            to = []
        for k in ("label", "help", "back"):
            if k in x and not isinstance(x[k], str):
                problems.append(f"{where}: {k} is text")
        if "many" in x and not isinstance(x["many"], bool):
            problems.append(f"{where}: many is true or false")
        out.append({"rel": rel, "label": str(x.get("label") or state_words(rel).capitalize()), "to": list(to),
                    "many": bool(x.get("many", True)), "help": str(x.get("help") or ""), "back": str(x.get("back") or ""),
                    "field": None})
    return out, problems


def links(rt: RecordType, rec: dict) -> list[tuple[str, str]]:
    """(rel, Type/id) for every reference the record holds."""
    out = []
    for f in rt.references:
        for v in get(rec, f["path"]):
            for item in (v if isinstance(v, list) else [v]):
                ref = ref_of(item)
                if ref:
                    out.append((f["rel"], ref))
    return out


FORM_SIMPLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.\*)?")  # a field a form can edit: a top-level key, or a list of them
# or one plain value inside a structure, by keys and list positions (name.0.given.0, telecom.0.value)
FORM_NESTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.([0-9]+|[A-Za-z_][A-Za-z0-9_]*))+")
SCALAR = ("string", "text", "markdown", "date", "datetime", "choice", "number", "decimal", "boolean")


def row_words(rt: RecordType, cell: dict, holder: dict, target: str) -> list[str]:
    """What else the rows of a graph column's table say beside the record it points at (``target``), in ``holder``
    (one of ``rt``'s records): "Quantity 2", "Designators R1, R2". Used where a graph is drawn or traced."""
    table = rt.field(cell["table"])
    if table is None:
        return []
    def word(v) -> str:
        if isinstance(v, decimals.Decimal):
            return decimals.text(v)
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return ref_of(v) or str(v)
    out = []
    for item in first(holder, table["path"]) or []:
        if isinstance(item, dict) and ref_of(item.get(cell["name"])) == target:
            out += [f"{c['label']} {word(item[c['name']])}" for c in table["columns"]
                    if c["name"] != cell["name"] and c.get("show", True) is not False
                    and item.get(c["name"]) not in (None, "", [], {})]
    return out


def form_fields(rt: RecordType) -> list[dict]:
    """The fields a person edits in a form: plain values at a path a form can write, not the lifecycle's state, not
    a workflow's, not files, not a list whose items are a field of their own."""
    return [f for f in rt.fields if (FORM_SIMPLE.fullmatch(f["path"]) or (FORM_NESTED.fullmatch(f["path"])
                                                                         and f["type"] in (*SCALAR, "reference")))
            and f.get("show", True) is not False and f.get("by") != "workflow"
            and f["type"] != "attachment" and not (rt.lifecycle and f["path"] == rt.lifecycle["path"])
            and not (f["type"] == "list" and any(g["path"] == f["path"] + ".*" for g in rt.fields))]


def list_columns(rt: RecordType) -> list[dict]:
    """The fields a list of a type's records shows beside its title and status: for a part, its owner first; else the
    one subject it is about (a visit's patient: whose it is); then a few short values. A list's export has the same
    columns."""
    heads = [f for f in form_fields(rt) if f["type"] in ("string", "choice", "date", "member", "number", "decimal")][:3]
    owner = rt.field(rt.part_of["field"]) if rt.part_of else None
    if owner is None:
        owner = next((f for f in rt.fields if f["type"] == "reference" and f.get("rel") == "subject"
                      and not f.get("list") and "*" not in f["path"] and f.get("show", True) is not False), None)
    if owner:
        heads = [owner] + [f for f in heads if f is not owner][:2]
    return heads


def record_date(rt: RecordType | None, rec: dict) -> str:
    v = first(rec, rt.date) if rt else None
    if isinstance(v, dict):  # a period
        v = v.get("start") or v.get("end")
    return str(v or (rec.get("meta") or {}).get("lastUpdated", ""))


def is_active(rt: RecordType | None, rec: dict) -> bool:
    if not rt or not rt.active:
        return True
    try:
        return expr.holds(rt.active, {"record": rec})
    except expr.ExprError:
        return True


# ------------------------------------------------------------------------------------------- module additions
# What a module adds to a type (or view, or workflow) another part of the practice owns sits in the text between
# these lines (``hub._extended_source`` writes them). The practice's own text is what lies outside them; a practice's
# change never adds, alters or removes what is inside, and the module's lines are put back after its text on save.
ADDED = re.compile(r"# >>> added by the ([a-z][a-z0-9-]*) module\n(.*?)\n# <<< \1(?:\n|$)", re.S)
ADDED_LINE = re.compile(r"^[ \t]*# (>>>|<<<)", re.M)


def module_additions(source: str) -> list[tuple[str, str]]:
    """``[(module id, the text it added)]``, in the order they stand."""
    return [(m, t.strip()) for m, t in ADDED.findall(source or "")]


def own_text(source: str) -> str:
    """A definition's text without what modules added to it: what the practice owns and edits."""
    out = ADDED.sub("", source or "")
    return re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"


def with_additions(own: str, additions: list[tuple[str, str]]) -> str:
    """The practice's text followed by each module's additions, marked as ``hub._extended_source`` marks them."""
    src = (own or "").rstrip() + "\n"
    for mid, text in additions:
        src += f"\n# >>> added by the {mid} module\n{text.strip()}\n# <<< {mid}\n"
    return src


def _added_names(text: str) -> dict[str, set]:
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    pick = lambda key, k: {str(x.get(k)) for x in doc.get(key, []) if isinstance(x, dict) and x.get(k)}  # noqa: E731
    return {"field": pick("field", "name"), "signature": pick("signature", "meaning"),
            "column": pick("column", "field"), "widget": pick("widget", "id")}


def keep_module_additions(source: str, in_force: str | None) -> tuple[str, list[str]]:
    """A practice's proposed text, with what modules added to the version in force put back after it.

    The practice owns only its own text: a proposal may leave the modules' marked lines out (they are re-applied) or
    carry them exactly as they are in force; one that adds, changes or removes them, or that defines in its own text
    a field (signature, column, widget) a module added, is refused. Returns (the text to propose, problems)."""
    sent, have = module_additions(source), module_additions(in_force or "")
    own, problems = own_text(source), []
    for n, line in enumerate(own.splitlines(), 1):
        if ADDED_LINE.match(line):
            problems.append(f"line {n}: {line.strip()!r} marks where a module's additions start or end; the practice's "
                            f"text does not carry it (leave the module's lines out: they are kept as they are)")
    held = dict(have)
    for mid, text in sent:
        if mid not in held:
            problems.append(f"the {mid} module: the text carries additions marked as the {mid} module's, which the "
                            f"version in force does not have; only the module adds them (install or upgrade it)")
        elif text != held[mid]:
            problems.append(f"the {mid} module: what it added cannot be changed here; it changes with the module "
                            f"(upgrade it), and the practice's own fields go outside its marked lines")
    if sent:
        for mid in held:
            if mid not in dict(sent):
                problems.append(f"the {mid} module: the text leaves out what the {mid} module added; a module's "
                                f"additions go only when the module is removed")
    mine = _added_names(own)
    for mid, text in have:
        for what, names in _added_names(text).items():
            for nm in sorted(names & mine.get(what, set())):
                problems.append(f"{what} {nm}: added by the {mid} module, so the practice's text does not define it "
                                f"again (to change it, change the module)")
    if have:
        return with_additions(own, have), problems
    return (own if sent else source), problems
