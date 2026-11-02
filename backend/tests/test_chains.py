"""Offline tests for the LangChain composition layer.

No network and no API key: the chains are exercised with stub models, which is
exactly how the planner/summariser are wired in production (prompt | model).
"""

import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableLambda

from app.chains.plan import PlanError, validate_plan
from app.chains.planner import build_planner, make_plan
from app.chains.runnable import AdapterRunnable, run_adapter
from app.chains.schemas import Plan
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


class _StubModel:
    """Mimics `BaseChatModel.with_structured_output` without a provider."""

    def __init__(self, payload: dict):
        self.payload = payload

    def with_structured_output(self, schema):
        return RunnableLambda(lambda _inputs: schema(**self.payload))


def _members():
    return [(_Member("executor"), _Agent(1, "小A")), (_Member("reviewer"), _Agent(2, "小B"))]


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


async def test_make_plan_uses_structured_output_chain():
    model = _StubModel({"steps": [{"id": "s1", "agent": "小A", "instruction": "做", "role": "executor"}]})
    plan = await make_plan(request="做个东西", members=_members(), model=model)
    assert plan[0]["agent_id"] == 1
    assert plan[0]["instruction"] == "做"


def test_build_planner_is_a_runnable():
    chain = build_planner(_StubModel({"steps": []}))
    assert hasattr(chain, "ainvoke")


async def test_summarizer_chain_returns_text():
    model = FakeListChatModel(responses=["## 结论\n全部完成"])
    chain = build_summarizer(model)
    text = await chain.ainvoke({"request": "任务", "results": "### a\nok"})
    assert "结论" in text

    text2 = await summarize_results(request="任务", results=[("步骤一", "小A", "done")], model=model)
    assert text2.strip()


class _DummyAdapter:
    type = "dummy"

    async def run(self, request, emit, cancel):
        await emit.__call__(None)  # exercise the async emit path
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


def test_plan_schema_defaults():
    plan = Plan(steps=[{"id": "s1", "agent": "A", "title": "t", "instruction": "i"}])
    assert plan.steps[0].role == "executor"
    assert plan.steps[0].depends_on == []
