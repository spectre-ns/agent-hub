"""The sponsor approves held high-risk work from a page."""

import re

from conftest import M
from test_rules import _high_risk_renewal
from practicehub.web.server import App


def test_the_sponsor_approves_from_the_page(hub):
    run, hold = _high_risk_renewal(hub)
    app = App(hub, dev=True)
    amara, jo = {"cookie": "ph_as=dr-amara"}, {"cookie": "ph_as=jo"}
    code, _, body = app.handle("GET", f"/items/{hold}", amara, b"")
    assert code == 200 and b"waits for you" in body and b"approve" in body
    code, _, body = app.handle("GET", f"/items/{hold}", {"cookie": "ph_as=dr-liam"}, b"")
    assert b"You cannot decide this" in body  # another physician sees it, and may not decide it
    code, _, body = app.handle("GET", f"/items/{hold}", jo, b"")
    assert code == 404 and b"renew" not in body.lower()  # the front desk may not see a prescription step at all
    code, _, body = app.handle("GET", f"/items/{hold}", amara, b"")
    seen = re.search(rb'name="seen" value="([0-9a-f]{64})"', body).group(1)
    form = {**amara, "content-type": "application/x-www-form-urlencoded"}
    code, _, _ = app.handle("POST", f"/items/{hold}/decide", form, b"outcome=approve&comment=ok")
    assert code == 422  # signed without having been shown the answer
    code, _, _ = app.handle("POST", f"/items/{hold}/decide", form, b"outcome=approve&comment=ok&seen=" + seen)
    assert code == 303
    assert hub.engine.run(run["id"])["steps"]["prepare"]["state"] == "done"
    code, _, body = app.handle("GET", "/", {"cookie": "ph_as=dr-amara"}, b"")
    assert code == 200


def test_a_decision_over_the_api_signs_what_the_context_showed(hub):
    import json
    run, hold = _high_risk_renewal(hub)
    app = App(hub, dev=True)
    amara = {"cookie": "ph_as=dr-amara", "content-type": "application/json"}
    code, _, _ = app.handle("POST", f"/api/items/{hold}/decide", amara, b'{"outcome": "approve"}')
    assert code == 422  # no seen
    code, _, body = app.handle("GET", f"/api/items/{hold}/context", amara, b"")
    seen = json.loads(body)["seen"]
    code, _, _ = app.handle("POST", f"/api/items/{hold}/decide", amara,
                            json.dumps({"outcome": "approve", "seen": seen}).encode())
    assert code == 200
    assert hub.engine.run(run["id"])["steps"]["prepare"]["state"] == "done"
    # outside dev mode a cookie is not a credential: it is sent without the signer meaning to
    run, hold = _high_risk_renewal(hub)
    strict = App(hub, dev=False)
    code, _, _ = strict.handle("POST", f"/api/items/{hold}/decide", amara,
                               json.dumps({"outcome": "approve", "seen": seen}).encode())
    assert code in (401, 403)
