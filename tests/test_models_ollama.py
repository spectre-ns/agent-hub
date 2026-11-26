"""The on-premises model (Ollama): what is sent, and an empty answer is an error that says why, not a JSON error."""
import pytest

from practicehub import models


def test_ollama_reads_the_whole_context_and_answers_without_thinking_first(monkeypatch):
    sent = {}

    def post(url, body, headers, timeout):
        sent.update(body)
        return {"message": {"content": '{"answer": {"category": "abnormal"}, "escalate": false, "reason": "flag H"}'}}

    monkeypatch.setattr(models, "_post", post)
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    monkeypatch.delenv("OLLAMA_THINK", raising=False)
    out = models.Ollama(url="http://ollama.invalid")("gemma", "sys", "prompt", {"type": "object"}, {})
    assert out["answer"] == {"category": "abnormal"}
    assert sent["options"]["num_ctx"] >= 32768 and sent["think"] is False


def test_an_empty_answer_says_the_model_gave_none(monkeypatch):
    monkeypatch.setattr(models, "_post", lambda *a: {"message": {"content": "", "thinking": "hmm"},
                                                      "done_reason": "length", "prompt_eval_count": 4096})
    with pytest.raises(models.ModelError, match="gave no answer.*length.*4096"):
        models.Ollama(url="http://ollama.invalid")("gemma", "sys", "prompt", {}, {})


def test_a_picture_goes_only_to_a_model_that_says_it_can_see(monkeypatch):
    import base64
    sent = []

    def post(url, body, headers, timeout):
        sent.append((url, body))
        if url.endswith("/api/show"):
            return {"capabilities": ["completion", "vision"] if body["model"] == "eyes" else ["completion"]}
        return {"message": {"content": '{"answer": {}, "escalate": false, "reason": "read"}'}}

    monkeypatch.setattr(models, "_post", post)
    o = models.Ollama(url="http://ollama.invalid")
    pic = [{"name": "slip.png", "contentType": "image/png", "data": b"\x89PNG\r\n\x1a\n..."}]
    o("eyes", "sys", "prompt", {}, {}, pictures=pic)
    assert sent[-1][1]["messages"][1]["images"] == [base64.b64encode(pic[0]["data"]).decode()]
    o("eyes", "sys", "prompt", {}, {}, pictures=pic)
    assert [u for u, _ in sent].count("http://ollama.invalid/api/show") == 1  # a model is asked once
    with pytest.raises(models.ModelError, match="does not take pictures"):
        o("words", "sys", "prompt", {}, {}, pictures=pic)
    o("words", "sys", "prompt", {}, {})  # no picture: nothing to ask
    assert "images" not in sent[-1][1]["messages"][1]


def test_litellm_is_sent_a_picture_as_a_data_url(monkeypatch):
    import base64
    sent = {}

    def post(url, body, headers, timeout):
        sent.update(body)
        return {"choices": [{"message": {"content": '{"answer": {}, "escalate": false, "reason": "read"}'}}]}

    monkeypatch.setattr(models, "_post", post)
    pic = {"name": "slip.jpg", "contentType": "image/jpeg", "data": b"\xff\xd8\xff..."}
    models.LiteLLM(url="http://litellm.invalid", key="")("vision", "sys", "prompt", {}, {"step": {"id": "s"}},
                                                         pictures=[pic])
    content = sent["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "prompt"}
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(pic["data"]).decode()
