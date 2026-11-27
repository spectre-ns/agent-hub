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
    assert sent["options"]["num_ctx"] == 32768 and sent["think"] is False
    sent.clear()
    models.Ollama(url="http://ollama.invalid").chat("gemma", "sys", [{"role": "user", "text": "hi"}], [], {})
    assert sent["options"]["num_ctx"] == 32768  # a chat turn and a mention reply get the same window


def test_every_local_model_answers_with_the_sampling_it_ships_with(monkeypatch):
    """The hub sets no temperature, top_p or top_k: greedy decoding (temperature 0) makes a model that thinks repeat
    itself until its window is full, so each model keeps the settings it ships with, in a step and in a chat."""
    sent = []
    monkeypatch.setattr(models, "_post", lambda url, body, headers, timeout: sent.append(body) or {
        "message": {"content": '{"answer": {}, "escalate": false, "reason": ""}'},
        "choices": [{"message": {"content": '{"answer": {}, "escalate": false, "reason": ""}'}}]})
    rs = [{"role": "user", "text": "hi"}]
    models.Ollama(url="http://ollama.invalid")("gemma", "sys", "prompt", {}, {})
    models.Ollama(url="http://ollama.invalid").chat("gemma", "sys", rs, [], {})
    models.LiteLLM(url="http://litellm.invalid")("gemma", "sys", "prompt", {}, {"step": {"id": "s"}})
    models.LiteLLM(url="http://litellm.invalid").chat("gemma", "sys", rs, [], {})
    assert len(sent) == 4
    for body in sent:
        assert not {"temperature", "top_p", "top_k", "seed"} & (set(body) | set(body.get("options") or {})), body


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


def test_ollama_lists_its_installed_models_and_which_see(monkeypatch):
    def get(url, headers, timeout):
        assert url == "http://ollama.invalid/api/tags" and timeout <= 3
        return {"models": [{"name": "qwen3:14b"}, {"name": "gemma4:12b-it-qat"}, {"model": "granite4.2:3b"}]}

    monkeypatch.setattr(models, "_get", get)
    monkeypatch.setattr(models, "_post", lambda url, body, headers, timeout: {
        "capabilities": ["completion", "vision"] if body["model"].startswith("gemma") else ["completion"]})
    assert models.Ollama(url="http://ollama.invalid").models() == [
        {"name": "gemma4:12b-it-qat", "sees": True}, {"name": "granite4.2:3b", "sees": False},
        {"name": "qwen3:14b", "sees": False}]


def test_the_gateway_offers_what_its_servers_serve_and_names_one_that_does_not_answer(monkeypatch):
    """Ollama's models, LiteLLM's only when its proxy is configured, Claude's only when the practice allows models off
    the premises, and always ``standin``: what an agent's profile form offers."""
    class Served:
        def __init__(self, external, names):
            self.external, self.names = external, names

        def models(self):
            if self.names is None:
                raise models.ModelError("cannot reach it")
            return [{"name": n, "sees": True} for n in self.names]

    gw = models.ModelGateway(allow_external=False)
    gw.backends = {"ollama": Served(False, ["gemma4:12b-it-qat"]), "litellm": Served(False, None),
                   "anthropic": Served(True, ["claude-opus-5-5"])}
    got = gw.offered()
    assert [m["spec"] for m in got["models"]] == ["ollama/gemma4:12b-it-qat", "standin"]
    assert got["unreached"] == {"litellm": "cannot reach it"}
    gw.backends["ollama"].names = ["qwen3:14b"]
    assert gw.offered() is got  # kept a while: a page is not held up asking
    monkeypatch.setattr(gw, "OFFERED_FOR", 0)
    gw.allow_external = True
    assert [m["spec"] for m in gw.offered()["models"]] == ["ollama/qwen3:14b", "anthropic/claude-opus-5-5", "standin"]
    monkeypatch.delenv("LITELLM_URL", raising=False)
    assert models.LiteLLM().models() == []  # no proxy configured: nothing asked, nothing offered


def test_a_model_is_given_as_long_as_it_takes(monkeypatch):
    """Agents and chats answer in the background, so no call to a model has a time limit, on any backend. Asking a
    server which models it has does: a page waits on that."""
    import io
    import json
    answer = '{"answer": {}, "escalate": false, "reason": "done"}'
    said = {"message": {"content": answer}, "choices": [{"message": {"content": answer}}],
            "content": [{"type": "text", "text": answer}], "stop_reason": "end_turn",
            "models": [{"name": "gemma"}], "capabilities": ["completion"], "data": [{"id": "gemma"}]}
    waited = {}

    class Said(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    def urlopen(req, timeout):
        waited.setdefault(req.full_url.split("/", 3)[3], []).append(timeout)
        return Said(json.dumps(said).encode())

    monkeypatch.setattr(models.urllib.request, "urlopen", urlopen)
    o = models.Ollama(url="http://ollama.invalid")
    o("gemma", "sys", "prompt", {}, {})
    o.chat("gemma", "sys", [{"role": "user", "text": "hello"}], [], {})
    models.LiteLLM(url="http://litellm.invalid")("gemma", "sys", "prompt", {}, {"step": {"id": "s"}})
    models.Anthropic(api_key="k", base_url="http://anthropic.invalid")("claude", "sys", "prompt", {}, {})
    assert waited.pop("api/chat") == [None, None]
    assert waited.pop("v1/chat/completions") == [None] and waited.pop("v1/messages") == [None]
    o.models()
    assert waited.pop("api/tags") == [3] and waited.pop("api/show") == [3]
    assert not waited
