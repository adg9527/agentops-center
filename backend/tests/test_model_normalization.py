"""OpenCode model ids must be `provider/model`; a bare name is completed, not rejected."""

import pytest

from app.adapters import opencode as oc
from app.adapters.opencode import pick_model


@pytest.mark.parametrize(
    "models, model, expected",
    [
        (["opencode/a", "opencode-go/a"], "a", "opencode/a"),
        (["opencode-go/b"], "b", "opencode-go/b"),
        (["zzz/c", "opencode/c"], "c", "opencode/c"),
        (["opencode/a"], "opencode/a", "opencode/a"),
        (["opencode/a"], "unknown", "unknown"),
        ([], "a", "a"),
        (["opencode/a"], "", ""),
    ],
)
def test_pick_model(models, model, expected):
    assert pick_model(models, model) == expected


@pytest.fixture
def adapter(monkeypatch):
    a = oc.OpenCodeAdapter()
    detected = ["opencode/deepseek-v4.1-flash", "opencode-go/deepseek-v4.1-flash"]

    async def fake_models(exe, refresh=False):
        return detected

    monkeypatch.setattr(oc, "resolve_opencode", lambda cfg: "opencode.exe")
    monkeypatch.setattr(a, "list_models", fake_models)
    return a


async def test_normalize_model_completes_bare_name(adapter):
    assert await adapter.normalize_model("deepseek-v4.1-flash", {}) == "opencode/deepseek-v4.1-flash"


async def test_normalize_model_keeps_full_id(adapter):
    assert await adapter.normalize_model("opencode-go/deepseek-v4.1-flash", {}) == "opencode-go/deepseek-v4.1-flash"


async def test_validate_model_accepts_bare_name_once_resolvable(adapter):
    assert await adapter.validate_model("deepseek-v4.1-flash", {}) is None


async def test_validate_model_rejects_unknown_bare_name(adapter):
    err = await adapter.validate_model("no-such-model", {})
    assert err and "provider/模型名" in err


async def test_validate_model_rejects_unknown_full_id(adapter):
    err = await adapter.validate_model("opencode/no-such-model", {})
    assert err and "没有模型" in err
