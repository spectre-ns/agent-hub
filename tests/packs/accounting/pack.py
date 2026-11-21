"""Lighthouse Accounting: a stand-in answer and a summary. Nothing here is medical."""


def summarize(rec):
    if rec.get("resourceType") == "TaxSlip":
        return f"{rec.get('kind')} {rec.get('amount')}"
    return None  # the core names the rest from their types


def standin_return_prep(ctx):
    slips = [r for r in ctx["context"].get("record:tax", []) if r.get("resourceType") == "TaxSlip"]
    if not slips:
        return {"answer": {}, "escalate": True, "reason": "no slips on file"}
    total = sum(float(s.get("amount", 0)) for s in slips)
    return {"answer": {"income": total, "summary": f"{len(slips)} slip(s), total income {total:.2f}"},
            "reason": "added up the slips"}


STANDINS = {"return-prep": standin_return_prep}
