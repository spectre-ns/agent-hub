"""BPMN on the workflow pages: download a workflow as a BPMN 2.0 file, and import one into the editor.

An import never installs anything: it fills the workflow editor with the TOML the file gives, and lists what could
not be brought across. The person reviews it and proposes it; a policy owner who did not write it approves it, as
for any edit.
"""

from __future__ import annotations

import html
import re

from .. import bpmn
from ..hub import Invalid, NotFound
from .records_pages import Download, multipart


def e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


class BpmnPages:
    def bpmn_routes(self, R):
        R("GET", r"/workflows/(?P<wid>[\w-]+)/bpmn", self.page_workflow_bpmn)
        R("POST", r"/workflows/import-bpmn", self.post_import_bpmn)

    @staticmethod
    def bpmn_download_link(wid: str) -> str:
        return f'<a class="btn secondary" href="/workflows/{e(wid)}/bpmn" download>Download as BPMN</a>'

    @staticmethod
    def bpmn_import_form() -> str:
        return ('<form method="post" action="/workflows/import-bpmn" enctype="multipart/form-data" class="inline">'
                '<label class="btn secondary" style="cursor:pointer">Import BPMN '
                '<input type="file" name="file" accept=".bpmn,.xml,application/xml,text/xml" required '
                'onchange="this.form.submit()" style="width:0;height:0;opacity:0;position:absolute"></label>'
                '<noscript><button class="secondary">Import</button></noscript></form>')

    def page_workflow_bpmn(self, req, wid):
        """The workflow in force, as a BPMN file for Camunda Modeler, bpmn.io and other BPMN tools."""
        try:
            wf, row = self.hub.workflow(wid)
        except NotFound:
            raise NotFound(f"there is no workflow {wid!r} in force") from None
        body = bpmn.export_bpmn(wf).encode("utf-8")
        name = re.sub(r"[^a-z0-9-]", "", wid) or "workflow"
        return Download(({"Content-Type": "application/xml; charset=utf-8",
                          "Content-Disposition": f'attachment; filename="{name}-v{row["version"]}.bpmn"',
                          "Content-Security-Policy": "default-src 'none'; sandbox"}, body))

    def post_import_bpmn(self, req):
        """A BPMN file, read into workflow TOML and opened in the editor: nothing changes until it is proposed and
        approved."""
        form = multipart(req)
        f = form.get("file")
        if not isinstance(f, tuple) or not f[2]:
            raise Invalid("Choose a BPMN file to import.")
        filename, _, data = f
        if len(data) > bpmn.MAX_BYTES:
            raise Invalid(f"The file is too large for a workflow (over {bpmn.MAX_BYTES // (1024 * 1024)} MB).")
        try:
            out = bpmn.import_bpmn(data, self.hub.pack)
        except bpmn.BpmnError as exc:
            raise Invalid("The file could not be imported.", exc.problems) from None
        wid = None
        try:
            import tomllib
            wid = tomllib.loads(out.source).get("id")
        except Exception:  # noqa: BLE001 - the editor shows the text either way
            wid = None
        wid = wid if isinstance(wid, str) and self.hub.versions(wid) else None
        note = f"Imported from {filename}"[:200] if filename else "Imported from a BPMN file"
        page = self.page_workflow_edit(req, wid, source=out.source, note=note, problems=out.problems)
        notice = ('<div class="card" role="status"><p><b>Imported from BPMN.</b> Nothing has changed yet: check the '
                  'workflow below and propose it. A policy owner who did not write it approves it before any run '
                  'uses it.</p>'
                  + ("<p>What could not be brought across, or still needs your attention:</p><ul>"
                     + "".join(f"<li>{e(p)}</li>" for p in out.problems) + "</ul>" if out.problems else "")
                  + "</div>")
        marker = '<div id="wf-editor">'
        return page.replace(marker, notice + marker, 1) if marker in page else page
