"""Agent eval runner.

SPEC § Eval plan: "The eval runner imports `run_agent` directly with a
`Fake*Repository`-backed `AgentServices` and asserts on the emitted event stream.
It does not go through HTTP or SSE. This is only possible because the agent module
has a clean public interface."

Run it:

    pytest evals/                      # replay recorded turns (default, free)
    EVAL_MODE=record pytest evals/     # re-record from the live API — SPENDS MONEY
    EVAL_MODE=live   pytest evals/     # live, write nothing (drift job)

In replay mode a case with no cassette is **skipped**, not failed: a repo that has
never recorded should not report 30 red tests, and the metrics block prints how
many were actually graded so an empty run cannot be mistaken for a passing one.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from app.agent.events import Done, Error, TextDelta, ToolUse
from app.agent.llm_client import LLMClient
from app.agent.prompt import DEFAULT_PROMPT_VERSION
from app.agent.runner import ChatMessage, run_agent
from app.agent.services import AgentServices

from evals.cassette import (
    CassetteLLMClient,
    CassetteMissing,
    RecordingLLMClient,
    current_mode,
)
from evals.conftest import record_result
from evals.fixtures import (
    FIXED_NOW,
    OWNER_ID,
    EvalAlertRepository,
    EvalDeviceRepository,
    EvalReadingRepository,
)
from evals.grading import (
    EXPECTED_DISTRIBUTION,
    ActualCall,
    Case,
    CaseResult,
    grade_rubric,
    grade_tools,
    load_cases,
)

CASES = load_cases()


def test_case_file_matches_spec() -> None:
    """The case file must hold exactly the distribution SPEC § Eval plan fixes."""
    counts: dict[str, int] = {}
    for case in CASES:
        counts[case.category] = counts.get(case.category, 0) + 1
    assert counts == EXPECTED_DISTRIBUTION
    assert len(CASES) == 30


@dataclass
class Transcript:
    """What one `run_agent` call emitted, flattened for grading."""

    answer: str
    tool_calls: list[ActualCall]
    errors: list[str]
    latency_ms: int
    cost_usd: float
    prompt_version: str


def _build_llm(case: Case) -> tuple[LLMClient, RecordingLLMClient | None]:
    """The LLM client for this case, plus the recorder to flush if we are taping."""
    mode = current_mode()
    if mode == "replay":
        return CassetteLLMClient(case.id), None

    # Imported lazily: constructing it raises without a key, and replay mode must
    # never need one.
    from app.agent.llm_client import AnthropicLLMClient

    live = AnthropicLLMClient()
    if mode == "live":
        return live, None
    recorder = RecordingLLMClient(live, case.id, DEFAULT_PROMPT_VERSION)
    return recorder, recorder


async def _run(case: Case) -> Transcript:
    llm, recorder = _build_llm(case)

    services = AgentServices(
        devices=EvalDeviceRepository(),
        readings=EvalReadingRepository(),
        alerts=EvalAlertRepository(),
        llm=llm,
        # Pinned clock: "the last 24 hours" must mean the same window every run.
        now=lambda: FIXED_NOW,
    )

    text: list[str] = []
    calls: list[ActualCall] = []
    errors: list[str] = []
    done: Done | None = None

    async for event in run_agent(
        user_id=OWNER_ID,
        messages=[ChatMessage(role="user", content=case.question)],
        services=services,
        prompt_version=DEFAULT_PROMPT_VERSION,
    ):
        if isinstance(event, TextDelta):
            text.append(event.delta)
        elif isinstance(event, ToolUse):
            calls.append(ActualCall(name=event.name, input=dict(event.input)))
        elif isinstance(event, Error):
            errors.append(f"{event.code}: {event.message}")
        elif isinstance(event, Done):
            done = event

    if recorder is not None:
        recorder.save()

    assert done is not None, "run_agent must always emit Done"
    return Transcript(
        answer="".join(text),
        tool_calls=calls,
        errors=errors,
        latency_ms=done.usage.latency_ms,
        cost_usd=done.usage.cost_usd,
        prompt_version=done.prompt_version,
    )


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
async def test_case(case: Case) -> None:
    try:
        transcript = await _run(case)
    except CassetteMissing as exc:
        pytest.skip(str(exc))

    tools_ok, tool_failures = grade_tools(
        case.expect_tools,
        transcript.tool_calls,
        case.match,
        case.max_tools,
        case.forbid_tools,
    )
    rubric_ok, rubric_failures = grade_rubric(case.rubric, transcript.answer)

    result = CaseResult(
        case=case,
        tools_ok=tools_ok,
        rubric_ok=rubric_ok,
        tool_failures=tool_failures,
        rubric_failures=rubric_failures,
        answer=transcript.answer,
        tool_calls=transcript.tool_calls,
        latency_ms=transcript.latency_ms,
        cost_usd=transcript.cost_usd,
        prompt_version=transcript.prompt_version,
        errors=transcript.errors,
    )
    # Recorded before the assertion so the metrics block covers failures too.
    record_result(result)

    assert result.passed, _explain(result)


def _explain(result: CaseResult) -> str:
    """A failure message that shows what the model actually did."""
    lines = [
        "",
        f"case      {result.case.id} ({result.case.category})",
        f"question  {result.case.question}",
        f"tools     {[c.name for c in result.tool_calls]}",
    ]
    for call in result.tool_calls:
        lines.append(f"            {call.name}({call.input})")
    lines.append(f"answer    {result.answer.strip()[:400] or '(empty)'}")
    for problem in result.errors:
        lines.append(f"  ERROR   {problem}")
    for problem in result.tool_failures:
        lines.append(f"  TOOLS   {problem}")
    for problem in result.rubric_failures:
        lines.append(f"  RUBRIC  {problem}")
    return "\n".join(lines)
