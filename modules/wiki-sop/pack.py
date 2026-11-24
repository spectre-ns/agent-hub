"""Wiki to SOP: the step that files a PDF of a wiki page in document control as a new controlled document."""

from practicehub import pdf, schema


def file_page(engine, run, step, params, actor):
    """``wiki-sop.file-page``: a PDF of the page (``page``, a WikiPage reference) at its version now, as a new
    Document (in its type's first state: a draft) numbered ``number``, titled ``title`` (else the page's), owned by
    ``owner``. Refused while another document in use has that number: one its type's own workflow still walks, as
    the Document type says (never a list of states kept here). Returns the new document's reference."""
    hub = engine.hub
    ref = str(params.get("page") or "")
    if not ref.startswith("WikiPage/"):
        raise ValueError(f"wiki-sop.file-page: page is a wiki page, not {ref!r}")
    page = hub.records.read("WikiPage", ref.split("/", 1)[1])
    if not page:
        raise ValueError(f"wiki-sop.file-page: there is no {ref}")
    if page.get("status") == "archived":
        raise ValueError("an archived page is not made a controlled document: restore it first")
    number = str(params.get("number") or "").strip()
    if not number:
        raise ValueError("give the controlled document a number")
    rt = hub.record_type("Document")
    taken = [d for d in hub.records.search("Document", limit=None)
             if str(d.get("number") or "").strip().lower() == number.lower() and schema.starts_workflow(rt, d)]
    if taken:
        raise ValueError(f"{number} is already the number of a document in use: {hub.short_title(taken[0])}")
    name = str(page.get("title") or "")
    title = str(params.get("title") or "").strip() or name
    version = str((page.get("meta") or {}).get("versionId") or "")
    today = engine.now().date().isoformat()
    footer = (f'{number} {title}: made from the wiki page "{name}" (version {version}) on {today}. A printed copy is '
              f"not controlled: check document control for the revision in force.")
    data = pdf.render(f"{number} {title}", str(page.get("body") or ""), footer)
    att = hub.add_file(actor, "Document", data, f"{number} {title}.pdf", "application/pdf")
    doc = hub.create_record(actor, "Document", {
        "title": title, "number": number, "owner": params.get("owner") or page.get("owner"),
        "file": att,
        "change_summary": f"First issue, made from the wiki page {name}.",
        "body": (f"Made from the wiki page [[{ref}|{name}]] (version {version}) on {today}. The controlled text is "
                 f"the PDF under File; the wiki page is not controlled and may change after this."),
    }, run_id=run["id"])
    return {"reference": f"Document/{doc['id']}", "id": doc["id"], "page_version": version, "blob": att["blob"]}


ACTIONS = {"wiki-sop.file-page": file_page}


def demo(hub) -> None:
    """Demo only: the wiki's Hand hygiene page made a controlled document (SOP-021), a draft for its owner to send
    for review."""
    owners = [m for m in hub.members("person") if hub.is_policy_owner(m)]
    page = next((p for p in hub.records.search("WikiPage", limit=None) if p.get("title") == "Hand hygiene"), None)
    if not owners or not page:
        return
    hub.engine.start(owners[0], "wiki-sop-file", record=f"WikiPage/{page['id']}", inputs={"number": "SOP-021"})
