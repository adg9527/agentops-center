"""Structured-output schemas for the orchestration chains."""

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["analyst", "executor", "reviewer", "summarizer"]


class PlannedStep(BaseModel):
    """One unit of work handed to a team member."""

    id: str = Field(description="步骤 id，例如 s1；同一计划内唯一")
    agent: str = Field(description="执行该步骤的成员名称，必须来自给定的成员名单")
    role: Role = Field(default="executor", description="该步骤的角色：analyst/executor/reviewer/summarizer")
    title: str = Field(default="", description="不超过 30 字的简短标题；留空时用 instruction 前 60 字")
    instruction: str = Field(description="交给该成员的具体工作要求")
    depends_on: list[str] = Field(default_factory=list, description="必须先完成的步骤 id，无依赖留空")


class Plan(BaseModel):
    """A validated multi-agent plan."""

    steps: list[PlannedStep] = Field(description="按执行顺序排列的步骤；无依赖的步骤会并行执行")
