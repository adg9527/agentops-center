"""Offline tests for the LangChain orchestration layer.

No network and no API key: agent construction is checked against the installed
LangChain version, and the planning/summarising logic is driven by stub agents
that return the documented result shape.
"""

import asyncio

import pytest
from langchain.messages import AIMessage
from langchain_core.callbacks import UsageMetadataCallbackHandler

from app.chains.callbacks import PlatformCallbackHandler, build_callbacks, usage_totals
from app.chains.llm import build_chat_model
from app.chains.plan import PlanError, validate_plan
from app.chains.planner import build_planner, make_plan
from app.chains.runnable import AdapterRunnable, run_adapter
from app.chains.schemas import Plan, PlannedStep
from app.chains.summarizer import build_summarizer, summarize_results


class _Member:
    def __init__(self, role: str):
        self.role = role


class _Agent:
    def __init__(self, agent_id: int, name: str, tags: list[str] | None = None):
        self.id = agent_id
        self.name = name
        self.capability_tags = tags or ["code"]
        self.description = ""


class _StubAgent:
    """Stands in for a `create_agent` result: same `ainvoke` contract."""

    def __init__(self, result: dict):
        self.result = result
        self.calls: list[dict] = []

    async def ainvoke(self, inputs, config=None, **kwargs):
        self.calls.append({"inputs": inputs, "config": config})
        return self.result


def _members():
    return [(_Member("executor"), _Agent(1, "小A")), (_Member("reviewer"), _Agent(2, "小B"))]


# --------------------------------------------------------------------- models


def test_build_chat_model_parses_provider_prefix():
    openai_model = build_chat_model(provider="openai", model="gpt-4o-mini", api_key="sk-test")
    deepseek_model = build_chat_model(provider="deepseek", model="deepseek-chat", api_key="sk-test")
    assert type(openai_model).__name__ == "ChatOpenAI"
    assert "DeepSeek" in type(deepseek_model).__name__


def test_build_chat_model_rejects_unknown_provider():
    with pytest.raises(ValueError, match="未知的模型 provider"):
        build_chat_model(provider="nope", api_key="sk-test")


def test_agents_construct_without_network():
    model = build_chat_model(provider="openai", model="gpt-4o-mini", api_key="sk-test")
    assert hasattr(build_planner(model), "ainvoke")
    assert hasattr(build_summarizer(model), "ainvoke")


# --------------------------------------------------------------------- planner


def test_plan_schema_exposes_defaults():
    plan = Plan(steps=[{"id": "s1", "agent": "A", "title": "t", "instruction": "i"}])
    assert isinstance(plan.steps[0], PlannedStep)
    assert plan.steps[0].role == "executor"
    assert plan.steps[0].depends_on == []


async def test_make_plan_reads_structured_response():
    plan = Plan(steps=[PlannedStep(id="s1", agent="小A", title="做", instruction="做", role="executor")])
    stub = _StubAgent({"structured_response": plan})
    steps = await make_plan(request="做个东西", members=_members(), agent=stub)

    assert steps[0]["key"] == "s1"
    assert steps[0]["agent_id"] == 1
    assert steps[0]["instruction"] == "做"
    # the agent receives the documented message shape
    assert stub.calls[0]["inputs"]["messages"][0].content


async def test_make_plan_without_structured_response_raises():
    with pytest.raises(PlanError, match="structured_response"):
        await make_plan(request="x", members=_members(), agent=_StubAgent({}))


# ------------------------------------------------------------------ summariser


async def test_summarizer_returns_last_message_text():
    stub = _StubAgent({"messages": [AIMessage(content="ignored"), AIMessage(content="## 结论\n完成")]})
    text = await summarize_results(request="任务", results=[("步骤一", "小A", "done")], agent=stub)
    assert "结论" in text
    assert stub.calls[0]["inputs"]["messages"][0].content


# --------------------------------------------------------------- plan validation


@pytest.mark.parametrize(
    "steps, message",
    [
        ([], "没有给出任何步骤"),
        ([{"id": "s1", "agent": "不存在", "instruction": "x"}], "成员不存在"),
        ([{"id": "s1", "agent": "小A", "instruction": ""}], "缺少 instruction"),
        (
            [
                {"id": "s1", "agent": "小A", "instruction": "a", "depends_on": ["s2"]},
                {"id": "s2", "agent": "小B", "instruction": "b", "depends_on": ["s1"]},
            ],
            "循环依赖",
        ),
    ],
)
def test_validate_plan_rejects_bad_plans(steps, message):
    with pytest.raises(PlanError, match=message):
        validate_plan(steps, {"小A": 1, "小B": 2})


def test_validate_plan_normalises_names_and_roles():
    plan = validate_plan(
        [
            {"id": "s1", "agent": "小A", "instruction": "写代码", "role": "executor"},
            {"id": "s2", "agent": "小b", "instruction": "审查", "role": "reviewer", "depends_on": ["s1"]},
        ],
        {"小A": 1, "小B": 2},
    )
    assert [p["key"] for p in plan] == ["s1", "s2"]
    assert plan[1]["agent_id"] == 2  # case-insensitive member lookup
    assert plan[1]["role"] == "reviewer"
    assert plan[1]["title"] == "审查"  # falls back to the instruction


# ------------------------------------------------------------------- callbacks


def test_usage_totals_reads_official_usage_metadata():
    usage_cb = UsageMetadataCallbackHandler()
    usage_cb.usage_metadata = {
        "deepseek-chat": {"input_tokens": 12, "output_tokens": 30, "total_tokens": 42},
    }
    steps_cb = PlatformCallbackHandler()
    steps_cb.model = "deepseek-chat"
    steps_cb.calls = 1
    steps_cb.duration_ms = 250

    usage = usage_totals([steps_cb, usage_cb], events=steps_cb)
    assert (usage.input_tokens, usage.output_tokens) == (12, 30)
    assert usage.model == "deepseek-chat"
    assert usage.duration_ms == 250


def test_build_callbacks_returns_event_and_usage_handlers():
    handlers = build_callbacks()
    assert len(handlers) == 2
    assert any(isinstance(h, UsageMetadataCallbackHandler) for h in handlers)
    assert any(isinstance(h, PlatformCallbackHandler) for h in handlers)


# -------------------------------------------------------------------- runnable


class _DummyAdapter:
    type = "dummy"

    async def run(self, request, emit, cancel):
        await emit(None)  # exercise the async emit path
        return "result"


async def test_adapter_runnable_runs_through_langchain_runtime():
    async def emit(_event):
        return None

    adapter = _DummyAdapter()
    result = await run_adapter(adapter, object(), emit, asyncio.Event(), task_id=7)
    assert result == "result"

    runnable = AdapterRunnable(adapter, object(), emit, asyncio.Event())
    assert runnable.get_name() == "adapter:dummy"
    with pytest.raises(RuntimeError, match="只支持异步调用"):
        runnable.invoke({})
