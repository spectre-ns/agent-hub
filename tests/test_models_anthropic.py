"""The Anthropic backend's request shape (no network: the HTTP call is replaced)."""
from practicehub import models


def _capture(monkeypatch):
    sent = {}

    def fake_post(url, body, headers, timeout):
        sent.update(url=url, body=body, headers=headers)
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"answer": {}, "escalate": false}'}]}

    monkeypatch.setattr(models, "_post", fake_post)
    return sent


def test_haiku_gets_no_refusal_fallback(monkeypatch):
    sent = _capture(monkeypatch)
    models.Anthropic(api_key="test")("claude-haiku-5-5", "sys", "prompt", {"type": "object"}, {})
    assert "fallbacks" not in sent["body"] and "anthropic-beta" not in sent["headers"]
    assert sent["body"]["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in sent["body"]


def test_opus_gets_the_server_side_fallback(monkeypatch):
    sent = _capture(monkeypatch)
    models.Anthropic(api_key="test")("claude-opus-5-5", "sys", "prompt", {"type": "object"}, {})
    assert sent["body"]["fallbacks"] == "default"
    assert sent["headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01"


def test_a_refusal_escalates_to_people(monkeypatch):
    monkeypatch.setattr(models, "_post", lambda *a: {"stop_reason": "refusal", "content": []})
    out = models.Anthropic(api_key="test")("claude-haiku-5-5", "sys", "prompt", {"type": "object"}, {})
    assert out["escalate"] is True


def test_a_picture_is_an_image_block_before_the_words(monkeypatch):
    import base64
    sent = _capture(monkeypatch)
    pic = {"name": "slip.png", "contentType": "image/png", "data": b"\x89PNG\r\n\x1a\n..."}
    models.Anthropic(api_key="test")("claude-haiku-5-5", "sys", "prompt", {"type": "object"}, {}, pictures=[pic])
    content = sent["body"]["messages"][0]["content"]
    assert content[0] == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                       "data": base64.b64encode(pic["data"]).decode()}}
    assert content[1] == {"type": "text", "text": "prompt"}
