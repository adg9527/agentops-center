"""Ollama adapter: NDJSON streaming parser and model validation."""

import asyncio
import json

import pytest

from app.adapters import ADAPTERS
from app.adapters import ollama as mod
from app.adapters.ollama import OllamaAdapter, OllamaStreamParser


def _line(**payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


def test_parser_streams_chunks_and_usage():
    parser = OllamaStreamParser()
    first = parser.feed(_line(model="qwen3.5:4b", message={"role": "assistant", "content": "你"}, done=False))
    second = parser.feed(_line(model="qwen3.5:4b", message={"role": "assistant", "content": "好"}, done=False))
    last = parser.feed(_line(model="qwen3.5:4b", message={"role": "assistant", "content": ""}, done=True,
                             prompt_eval_count=5, eval_count=2, total_duration=1_234_567))

    assert [e.kind for e in first + second] == ["agent.message", "agent.message"]
    assert [e.output for e in first + second] == ["你", "好"]
    assert last[0].kind == "model.call" and last[0].attributes["output_tokens"] == 2

    result = parser.finish()
    assert result.ok is True
    assert result.output == "你好"
    assert (result.input_tokens, result.output_tokens) == (5, 2)


def test_parser_reports_error_line():
    parser = OllamaStreamParser()
    events = parser.feed(_line(error="model 'nope' not found"))
    assert events[0].kind == "agent.error"
    result = parser.finish()
    assert result.ok is False and "not found" in (result.error or "")


def test_parser_without_done_is_failure():
    parser = OllamaStreamParser()
    parser.feed(_line(model="m", message={"content": "半句"}, done=False))
    result = parser.finish()
    assert result.ok is False
    assert "done" in (result.error or "")


def test_parser_empty_content_is_failure():
    parser = OllamaStreamParser()
    parser.feed(_line(model="m", message={"content": ""}, done=True))
    result = parser.finish()
    assert result.ok is False and "空内容" in (result.error or "")


def test_adapter_registered():
    assert ADAPTERS["ollama"].display_name.startswith("Ollama")


def test_validate_model_against_detected_list(monkeypatch):
    async def fake_get(url: str, timeout: float = 5.0):
        assert url.endswith("/api/tags")
        return {"models": [{"name": "qwen3.5:4b"}, {"name": "deepseek-r1:8b"}]}

    monkeypatch.setattr(mod, "_get_json", fake_get)
    adapter = OllamaAdapter()

    assert asyncio.run(adapter.validate_model("qwen3.5:4b", {})) is None
    err = asyncio.run(adapter.validate_model("nope:1b", {}))
    assert err and "没有模型" in err
    assert asyncio.run(adapter.validate_model("", {})) is not None


def test_check_connection_reports_unreachable(monkeypatch):
    async def boom(url: str, timeout: float = 5.0):
        raise ConnectionError("refused")

    monkeypatch.setattr(mod, "_get_json", boom)
    result = asyncio.run(OllamaAdapter().check_connection({"base_url": "http://127.0.0.1:1"}, {}))
    assert result.ok is False and "无法连接" in result.detail


def test_body_disables_thinking_by_default(tmp_path):
    """默认 think=false：否则推理模型会一直“思考”，几分钟都不出正文。"""
    from app.adapters.base import RunRequest

    adapter = OllamaAdapter()
    req = RunRequest(prompt="hi", workdir=tmp_path, config={}, secrets={}, model="qwen3.5:4b", timeout_sec=10)
    assert adapter._body(req)["think"] is False

    on = RunRequest(prompt="hi", workdir=tmp_path, config={"think": True}, secrets={}, model="qwen3.5:4b",
                    timeout_sec=10)
    assert adapter._body(on)["think"] is True
