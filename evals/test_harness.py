"""Tests for the eval harness itself.

These are not evals. The cases in `cases.yaml` measure the *model*; this file
proves the thing measuring it works — that the fixtures are deterministic and
filter correctly, that the graders accept and reject what they should, and that
a recorded cassette replays into a real `run_agent` run.

Without this, `pytest evals/` on a repo with no cassettes reports "1 passed, 30
skipped" and tells you nothing about whether the harness is sound.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from app.agent.llm_client import ToolCall
from app.schemas.reading import AggregateFn, AggregateWindow
from tests.agent_fakes import FakeLLMClient

from evals import cassette as cassette_module
from evals.cassette import CassetteLLMClient, CassetteMissing, RecordingLLMClient
from evals.fixtures import (
    FIXED_NOW,
    OWNER_ID,
    EvalAlertRepository,
    EvalDeviceRepository,
    EvalReadingRepository,
    device_id,
)
from evals.grading import (
    ActualCall,
    ExpectedTool,
    Rubric,
    grade_rubric,
    grade_tools,
    load_cases,
    match_arg,
)

# --- fixtures ---------------------------------------------------------------


def test_device_ids_are_stable_across_calls() -> None:
    """Cassettes record the UUID the model chose; it must not move between runs."""
    assert device_id("Pump-3") == device_id("Pump-3")
    assert device_id("Pump-3") != device_id("Pump-4")


async def test_series_is_deterministic() -> None:
    first = await EvalReadingRepository().query(
        OWNER_ID,
        device_id=device_id("Pump-3"),
        start=None,
        end=None,
        limit=10,
    )
    second = await EvalReadingRepository().query(
        OWNER_ID,
        device_id=device_id("Pump-3"),
        start=None,
        end=None,
        limit=10,
    )
    assert [r.value for r in first] == [r.value for r in second]
    assert first, "fixture produced no readings"


async def test_query_window_is_half_open() -> None:
    """start inclusive, end exclusive — matching the documented API semantics."""
    repo = EvalReadingRepository()
    everything = await repo.query(
        OWNER_ID, device_id=device_id("Pump-3"), start=None, end=None, limit=10_000
    )
    boundary = everything[len(everything) // 2].time

    from_boundary = await repo.query(
        OWNER_ID,
        device_id=device_id("Pump-3"),
        start=boundary,
        end=None,
        limit=10_000,
    )
    up_to_boundary = await repo.query(
        OWNER_ID,
        device_id=device_id("Pump-3"),
        start=None,
        end=boundary,
        limit=10_000,
    )
    assert boundary in [r.time for r in from_boundary]
    assert boundary not in [r.time for r in up_to_boundary]


async def test_query_returns_newest_first() -> None:
    rows = await EvalReadingRepository().query(
        OWNER_ID, device_id=device_id("Furnace-1"), start=None, end=None, limit=5
    )
    assert [r.time for r in rows] == sorted((r.time for r in rows), reverse=True)


async def test_aggregate_respects_window_and_function() -> None:
    repo = EvalReadingRepository()
    pump3 = device_id("Pump-3")
    start = FIXED_NOW - timedelta(days=1)

    async def roll(window: AggregateWindow, fn: AggregateFn) -> list[float]:
        buckets = await repo.aggregate(
            OWNER_ID,
            device_id=pump3,
            window=window,
            fn=fn,
            start=start,
            end=FIXED_NOW,
        )
        return [bucket.value for bucket in buckets]

    hourly = await roll(AggregateWindow.HOUR, AggregateFn.AVG)
    daily = await roll(AggregateWindow.DAY, AggregateFn.AVG)
    # A day of data is many hourly buckets but at most two daily ones.
    assert len(hourly) > len(daily)

    minimum = await roll(AggregateWindow.DAY, AggregateFn.MIN)
    maximum = await roll(AggregateWindow.DAY, AggregateFn.MAX)
    assert min(minimum) <= min(maximum)


async def test_aggregate_window_differences_are_visible() -> None:
    """The whole point of the eval fixtures: a different window changes the answer."""
    repo = EvalReadingRepository()
    narrow = await repo.aggregate(
        OWNER_ID,
        device_id=device_id("Furnace-1"),
        window=AggregateWindow.DAY,
        fn=AggregateFn.AVG,
        start=FIXED_NOW - timedelta(days=1),
        end=FIXED_NOW,
    )
    wide = await repo.aggregate(
        OWNER_ID,
        device_id=device_id("Furnace-1"),
        window=AggregateWindow.DAY,
        fn=AggregateFn.AVG,
        start=FIXED_NOW - timedelta(days=7),
        end=FIXED_NOW,
    )
    assert len(wide) > len(narrow)


async def test_alerts_exist_and_filter_by_device_and_time() -> None:
    repo = EvalAlertRepository()
    recent = await repo.recent(
        OWNER_ID, since=FIXED_NOW - timedelta(days=1), device_id=None, limit=100
    )
    assert recent, "fixture should contain deliberate threshold breaches"
    assert all(a.time >= FIXED_NOW - timedelta(days=1) for a in recent)

    pump3 = await repo.recent(
        OWNER_ID,
        since=FIXED_NOW - timedelta(days=7),
        device_id=device_id("Pump-3"),
        limit=100,
    )
    assert pump3 and {a.device_name for a in pump3} == {"Pump-3"}

    none_recent = await repo.recent(
        OWNER_ID, since=FIXED_NOW + timedelta(hours=1), device_id=None, limit=100
    )
    assert none_recent == []


async def test_repositories_reject_a_foreign_user() -> None:
    with pytest.raises(AssertionError):
        await EvalDeviceRepository().list_for_user(uuid.uuid4())


# --- grading ---------------------------------------------------------------


def test_match_arg_pattern_forms() -> None:
    assert match_arg("*", "anything")
    assert match_arg("1h", "1h")
    assert match_arg("AVG", "avg"), "comparison is case-insensitive"
    assert not match_arg("1h", "1d")
    assert match_arg("device:Pump-3", str(device_id("Pump-3")))
    assert not match_arg("device:Pump-3", str(device_id("Pump-4")))
    assert not match_arg("device:Pump-3", "not-a-uuid")
    assert match_arg("re:^2026-03-01", "2026-03-01T06:00:00+00:00")
    assert not match_arg("re:^2026-03-01", "2026-02-28T06:00:00+00:00")


def test_exact_mode_rejects_extra_and_reordered_calls() -> None:
    expected = (ExpectedTool("list_devices"), ExpectedTool("query_readings"))
    ok, _ = grade_tools(
        expected,
        [ActualCall("list_devices", {}), ActualCall("query_readings", {})],
        "exact",
        None,
    )
    assert ok

    reordered, problems = grade_tools(
        expected,
        [ActualCall("query_readings", {}), ActualCall("list_devices", {})],
        "exact",
        None,
    )
    assert not reordered and problems

    extra, _ = grade_tools(
        expected,
        [
            ActualCall("list_devices", {}),
            ActualCall("query_readings", {}),
            ActualCall("query_readings", {}),
        ],
        "exact",
        None,
    )
    assert not extra


def test_ordered_mode_allows_extra_calls_but_not_a_missing_one() -> None:
    expected = (ExpectedTool("list_devices"), ExpectedTool("aggregate_window"))
    ok, _ = grade_tools(
        expected,
        [
            ActualCall("list_devices", {}),
            ActualCall("query_readings", {}),
            ActualCall("aggregate_window", {}),
        ],
        "ordered",
        None,
    )
    assert ok

    missing, problems = grade_tools(
        expected, [ActualCall("list_devices", {})], "ordered", None
    )
    assert not missing and problems


def test_ordered_mode_pairs_arg_aware_when_the_model_fans_out() -> None:
    """Several calls per device must not shift the pairing to the wrong one."""
    expected = (
        ExpectedTool("list_devices"),
        ExpectedTool("aggregate_window", {"device_id": "device:Furnace-1"}),
        ExpectedTool("aggregate_window", {"device_id": "device:Furnace-2"}),
    )
    f1, f2 = str(device_id("Furnace-1")), str(device_id("Furnace-2"))
    fanned_out = [
        ActualCall("list_devices", {}),
        ActualCall("aggregate_window", {"device_id": f1}),
        ActualCall("aggregate_window", {"device_id": f1}),
        ActualCall("aggregate_window", {"device_id": f1}),
        ActualCall("aggregate_window", {"device_id": f2}),
        ActualCall("aggregate_window", {"device_id": f2}),
    ]
    ok, problems = grade_tools(expected, fanned_out, "ordered", None)
    assert ok, problems

    # The relative order still matters: Furnace-2 before any Furnace-1 must fail.
    reversed_order, problems = grade_tools(
        expected,
        [
            ActualCall("list_devices", {}),
            ActualCall("aggregate_window", {"device_id": f2}),
        ],
        "ordered",
        None,
    )
    assert not reversed_order and problems


def test_forbidden_tools_are_reported() -> None:
    ok, problems = grade_tools(
        (ExpectedTool("list_devices"),),
        [
            ActualCall("list_devices", {}),
            ActualCall("query_readings", {"device_id": str(uuid.uuid4())}),
        ],
        "ordered",
        None,
        ("query_readings", "aggregate_window"),
    )
    assert not ok
    assert any("forbids" in problem for problem in problems)

    clean, _ = grade_tools(
        (ExpectedTool("list_devices"),),
        [ActualCall("list_devices", {})],
        "ordered",
        None,
        ("query_readings",),
    )
    assert clean


def test_must_match_covers_phrasings_a_substring_list_would_miss() -> None:
    """One alternation should accept every way a model might say "no such device"."""
    pattern = (
        r"(?i)\b(?:no|not|don'?t|doesn'?t|cannot|can'?t|unable)\b"
        r"(?:\W+\w+){0,6}?\W+(?:see|find|have|exist|locate|device|record)"
    )
    rubric = Rubric(must_match=(pattern,))
    for phrasing in (
        'I don\'t see a device named "Compressor-9" in your account.',
        "There is no device called Compressor-9 in your fleet.",
        "I can't find Compressor-9 among your devices.",
        "Compressor-9 does not exist in your account.",
        "I was unable to locate a device by that name.",
        "I have no record of Compressor-9.",
    ):
        ok, problems = grade_rubric(rubric, phrasing)
        assert ok, f"{phrasing!r}: {problems}"

    # A fabricated answer has no negation anywhere, so it must still fail.
    rejected, problems = grade_rubric(
        rubric, "Compressor-9 averaged 4.2 bar over the last hour."
    )
    assert not rejected and problems


def test_tool_agnostic_case_still_rejects_an_ungrounded_answer() -> None:
    """That case accepts either read tool, so its rubric carries the weight."""
    case = next(c for c in load_cases() if c.id == "recall-furnace2-yesterday")

    grounded = "On 2026-02-28 (yesterday), Furnace-2 ranged from 679.67°C to 899.96°C."
    ok, problems = grade_rubric(case.rubric, grounded)
    assert ok, problems

    for ungrounded in (
        "Furnace-2 looked normal yesterday.",  # right day, no reading
        "On 2026-02-28 Furnace-2 was fine.",  # date digits must not count as a value
        "Furnace-2 ranged from 679.67°C to 899.96°C.",  # a reading, but no window
        "I was unable to retrieve Furnace-2 data.",
    ):
        rejected, problems = grade_rubric(case.rubric, ungrounded)
        assert not rejected, f"rubric wrongly accepted: {ungrounded!r}"
        assert problems


def test_loader_rejects_a_broken_must_match_regex(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "cases:\n"
        "  - id: x\n"
        "    category: simple_recall\n"
        "    question: hi\n"
        "    rubric:\n"
        "      must_match: ['(unclosed']\n"
    )
    with pytest.raises(ValueError, match="bad must_match regex"):
        load_cases(bad)


def test_set_mode_ignores_order_and_still_checks_arguments() -> None:
    expected = (
        ExpectedTool("aggregate_window", {"device_id": "device:Pump-3"}),
        ExpectedTool("aggregate_window", {"device_id": "device:Pump-4"}),
    )
    ok, _ = grade_tools(
        expected,
        [
            ActualCall("aggregate_window", {"device_id": str(device_id("Pump-4"))}),
            ActualCall("aggregate_window", {"device_id": str(device_id("Pump-3"))}),
        ],
        "set",
        None,
    )
    assert ok

    wrong, problems = grade_tools(
        expected,
        [
            ActualCall("aggregate_window", {"device_id": str(device_id("Pump-4"))}),
            ActualCall("aggregate_window", {"device_id": str(device_id("Furnace-1"))}),
        ],
        "set",
        None,
    )
    assert not wrong and problems


def test_missing_argument_is_a_failure_not_a_pass() -> None:
    ok, problems = grade_tools(
        (ExpectedTool("query_readings", {"device_id": "*"}),),
        [ActualCall("query_readings", {})],
        "exact",
        None,
    )
    assert not ok
    assert any("missing argument" in problem for problem in problems)


def test_max_tools_ceiling() -> None:
    ok, problems = grade_tools((), [ActualCall("list_devices", {})] * 4, "ordered", 3)
    assert not ok
    assert any("ceiling" in problem for problem in problems)


def test_rubric_checks_all_three_clauses() -> None:
    rubric = Rubric(
        must_include=("Pump-3",),
        must_include_any=("bar", "pressure"),
        must_not_include=("will fail",),
    )
    ok, _ = grade_rubric(rubric, "Pump-3 averaged 5.2 bar today.")
    assert ok

    assert not grade_rubric(rubric, "Pump-4 averaged 5.2 bar.")[0]
    assert not grade_rubric(rubric, "Pump-3 looked normal.")[0]
    assert not grade_rubric(rubric, "Pump-3 at 5 bar and will fail soon.")[0]
    assert not grade_rubric(rubric, "   ")[0]


def test_loader_rejects_a_typo_in_a_rubric_key(tmp_path: Path) -> None:
    """A silently-ignored rubric key would make a case pass while checking nothing."""
    bad = tmp_path / "cases.yaml"
    bad.write_text(
        "cases:\n"
        "  - id: x\n"
        "    category: simple_recall\n"
        "    question: hi\n"
        "    rubric:\n"
        "      must_includ: [oops]\n"
    )
    with pytest.raises(ValueError, match="unknown rubric key"):
        load_cases(bad)


def test_loader_rejects_duplicate_ids_and_bad_match_mode(tmp_path: Path) -> None:
    duplicate = tmp_path / "dup.yaml"
    duplicate.write_text(
        "cases:\n"
        "  - {id: x, category: simple_recall, question: a}\n"
        "  - {id: x, category: simple_recall, question: b}\n"
    )
    with pytest.raises(ValueError, match="duplicate case id"):
        load_cases(duplicate)

    bad_mode = tmp_path / "mode.yaml"
    bad_mode.write_text(
        "cases:\n  - {id: y, category: simple_recall, question: a, match: fuzzy}\n"
    )
    with pytest.raises(ValueError, match="match must be one of"):
        load_cases(bad_mode)


# --- cassette round trip ---------------------------------------------------


async def test_record_then_replay_drives_a_real_run_agent_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Record a cassette from a scripted fake, then replay it through `run_agent`.

    This is the harness's end-to-end proof: it exercises `RecordingLLMClient`,
    the on-disk format, `CassetteLLMClient`, the eval fixtures, the real agent
    loop, and both graders — with no API key and no network.
    """
    monkeypatch.setattr(cassette_module, "CASSETTE_DIR", tmp_path)

    case = next(c for c in load_cases() if c.id == "recall-list-devices")

    # A scripted model: call list_devices, then answer citing the fleet.
    scripted = FakeLLMClient(
        [
            [ToolCall(id="toolu_1", name="list_devices", input={})],
            "Furnace-1, Furnace-2, Pump-3, Pump-4 and Inlet-Flow-1 are reporting.",
        ]
    )
    recorder = RecordingLLMClient(scripted, case.id, "v1")

    # Drive the recorder through the real loop so the cassette holds real turns.
    from app.agent.runner import ChatMessage, run_agent
    from app.agent.services import AgentServices

    services = AgentServices(
        devices=EvalDeviceRepository(),
        readings=EvalReadingRepository(),
        alerts=EvalAlertRepository(),
        llm=recorder,
        now=lambda: FIXED_NOW,
    )
    async for _ in run_agent(
        user_id=OWNER_ID,
        messages=[ChatMessage(role="user", content=case.question)],
        services=services,
    ):
        pass
    written = recorder.save()
    assert written.exists()

    # Now replay it and grade the result, exactly as `pytest evals/` would.
    monkeypatch.setenv("EVAL_MODE", "replay")
    from evals.runner import _run

    transcript = await _run(case)

    assert not transcript.errors
    assert [c.name for c in transcript.tool_calls] == ["list_devices"]
    tools_ok, tool_problems = grade_tools(
        case.expect_tools, transcript.tool_calls, case.match, case.max_tools
    )
    rubric_ok, rubric_problems = grade_rubric(case.rubric, transcript.answer)
    assert tools_ok, tool_problems
    assert rubric_ok, rubric_problems


def test_metrics_summary_computes_the_spec_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPEC § Eval plan names the tracked metrics; this is the arithmetic for them."""
    from evals import conftest as conftest_module
    from evals.grading import Case, CaseResult

    def result(case_id: str, tools_ok: bool, rubric_ok: bool, ms: int) -> CaseResult:
        return CaseResult(
            case=Case(
                id=case_id,
                category="simple_recall",
                question="q",
                expect_tools=(),
                rubric=Rubric(),
            ),
            tools_ok=tools_ok,
            rubric_ok=rubric_ok,
            tool_failures=[],
            rubric_failures=[],
            answer="a",
            tool_calls=[],
            latency_ms=ms,
            cost_usd=0.001,
            prompt_version="v1",
            errors=[],
        )

    monkeypatch.setattr(
        conftest_module,
        "_RESULTS",
        [
            result("a", True, True, 100),
            result("b", True, False, 200),
            result("c", False, True, 300),
            result("d", False, False, 400),
        ],
    )
    monkeypatch.setenv("EVAL_MODE", "replay")
    report = conftest_module._summarize()

    assert report["cases_graded"] == 4
    assert report["passed"] == 1
    assert report["tool_call_precision"] == 0.5
    assert report["rubric_pass_rate"] == 0.5
    assert report["latency_ms_p50"] == 300
    assert report["latency_ms_p95"] == 400
    assert report["mean_cost_usd"] == 0.001
    assert report["total_cost_usd"] == 0.004
    assert report["prompt_versions"] == {"v1": 4}
    assert report["by_category"] == {"simple_recall": {"total": 4, "passed": 1}}
    # Failures are itemised so a CI log says which cases regressed.
    assert {failure["id"] for failure in report["failures"]} == {"b", "c", "d"}


async def test_a_missing_cassette_is_reported_not_silently_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cassette_module, "CASSETTE_DIR", tmp_path)
    with pytest.raises(CassetteMissing, match="Record one with"):
        CassetteLLMClient("no-such-case")


async def test_an_exhausted_cassette_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the agent now wants more turns than were taped, say so."""
    monkeypatch.setattr(cassette_module, "CASSETTE_DIR", tmp_path)
    (tmp_path / "short.json").write_text('{"model": "m", "turns": []}')
    client = CassetteLLMClient("short")
    with pytest.raises(CassetteMissing, match="exhausted"):
        async for _ in client.stream_turn(
            system="s", messages=[], tools=[], now=FIXED_NOW
        ):
            pass
