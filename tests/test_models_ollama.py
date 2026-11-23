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
