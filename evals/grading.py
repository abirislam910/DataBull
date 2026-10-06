"""Scoring one eval case against the event stream `run_agent` emitted.

Two independent axes, per SPEC § Eval plan:

- **Tool-call precision** — did the model call the right tools, in the right
  order, with the right arguments? Arguments are matched as *patterns*, not
  values: the eval cares that `window` was `1h` and that `device_id` was
  Pump-3's, not what timestamp the model picked for `start`.
- **Rubric** — does the final prose contain what it must, and not contain what
  it must not?

A case passes only if both axes pass. They are reported separately because they
fail for different reasons: a tool miss is a schema or prompt problem, a rubric
miss is usually a grounding or brevity problem.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from evals.fixtures import device_id

MatchMode = Literal["exact", "ordered", "set"]

CASES_PATH = Path(__file__).parent / "cases.yaml"

# SPEC § Eval plan fixes the distribution. `test_case_file_matches_spec` asserts
# it, so drifting from the contract fails rather than quietly changing coverage.
EXPECTED_DISTRIBUTION: dict[str, int] = {
    "simple_recall": 12,
    "aggregation": 8,
    "multi_step": 5,
    "alert_aware": 3,
    "should_decline": 2,
}

_MATCH_MODES = ("exact", "ordered", "set")
_RUBRIC_KEYS = frozenset({"must_include", "must_include_any", "must_not_include"})


@dataclass(frozen=True)
class ExpectedTool:
    """One expected call: a name, and optionally argument patterns."""

    name: str
    args: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Rubric:
    must_include: tuple[str, ...] = ()
    must_include_any: tuple[str, ...] = ()
    must_not_include: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    question: str
    expect_tools: tuple[ExpectedTool, ...]
    rubric: Rubric
    match: MatchMode = "exact"
    max_tools: int | None = None


def _parse_rubric(raw: dict[str, Any], case_id: str) -> Rubric:
    unknown = set(raw) - _RUBRIC_KEYS
    if unknown:
        # A typo in a rubric key would otherwise silently weaken the case —
        # `must_include_al: [...]` would check nothing at all and still pass.
        raise ValueError(f"{case_id}: unknown rubric key(s) {sorted(unknown)}")
    return Rubric(
        must_include=tuple(raw.get("must_include", ())),
        must_include_any=tuple(raw.get("must_include_any", ())),
        must_not_include=tuple(raw.get("must_not_include", ())),
    )


def load_cases(path: Path | None = None) -> list[Case]:
    """Parse `cases.yaml` into `Case` objects, rejecting anything malformed."""
    document = yaml.safe_load((path or CASES_PATH).read_text())
    cases: list[Case] = []
    seen: set[str] = set()

    for raw in document["cases"]:
        case_id = str(raw["id"])
        if case_id in seen:
            raise ValueError(f"duplicate case id {case_id!r}")
        seen.add(case_id)

        mode = str(raw.get("match", "exact"))
        if mode not in _MATCH_MODES:
            raise ValueError(f"{case_id}: match must be one of {_MATCH_MODES}")

        tools = tuple(
            ExpectedTool(
                name=str(tool["name"]),
                args={str(k): str(v) for k, v in (tool.get("args") or {}).items()},
            )
            for tool in raw.get("expect_tools") or ()
        )

        cases.append(
            Case(
                id=case_id,
                category=str(raw["category"]),
                question=str(raw["question"]),
                expect_tools=tools,
                rubric=_parse_rubric(raw.get("rubric") or {}, case_id),
                match=cast("MatchMode", mode),
                max_tools=(
                    int(raw["max_tools"]) if raw.get("max_tools") is not None else None
                ),
            )
        )

    return cases


@dataclass(frozen=True)
class ActualCall:
    name: str
    input: dict[str, Any]


@dataclass
class CaseResult:
    case: Case
    tools_ok: bool
    rubric_ok: bool
    tool_failures: list[str]
    rubric_failures: list[str]
    answer: str
    tool_calls: list[ActualCall]
    latency_ms: int
    cost_usd: float
    prompt_version: str
    errors: list[str]

    @property
    def passed(self) -> bool:
        return self.tools_ok and self.rubric_ok and not self.errors


def match_arg(pattern: str, value: Any) -> bool:
    """Match one argument against a pattern.

    Forms:
      `*`               present, any value
      `device:<Name>`   equals that fixture device's stable UUID
      `re:<regex>`      regex search against `str(value)`
      anything else     case-insensitive equality against `str(value)`
    """
    if pattern == "*":
        return True
    if pattern.startswith("device:"):
        wanted = device_id(pattern.removeprefix("device:"))
        try:
            return uuid.UUID(str(value)) == wanted
        except (ValueError, AttributeError, TypeError):
            return False
    if pattern.startswith("re:"):
        return re.search(pattern.removeprefix("re:"), str(value)) is not None
    return str(value).strip().lower() == pattern.strip().lower()


def _args_failures(expected: ExpectedTool, actual: ActualCall) -> list[str]:
    problems: list[str] = []
    for key, pattern in expected.args.items():
        if key not in actual.input:
            problems.append(f"{expected.name}: missing argument {key!r}")
        elif not match_arg(pattern, actual.input[key]):
            problems.append(
                f"{expected.name}.{key}: expected {pattern!r}, "
                f"got {actual.input[key]!r}"
            )
    return problems


def grade_tools(
    expected: tuple[ExpectedTool, ...],
    actual: list[ActualCall],
    mode: MatchMode,
    max_tools: int | None,
) -> tuple[bool, list[str]]:
    """Compare the calls the model made against what the case expects."""
    problems: list[str] = []

    if max_tools is not None and len(actual) > max_tools:
        problems.append(f"made {len(actual)} tool calls, ceiling is {max_tools}")

    if mode == "exact":
        if [call.name for call in actual] != [exp.name for exp in expected]:
            problems.append(
                f"sequence {[c.name for c in actual]} != expected "
                f"{[e.name for e in expected]}"
            )
        else:
            for exp, got in zip(expected, actual, strict=True):
                problems.extend(_args_failures(exp, got))

    elif mode == "set":
        if sorted(call.name for call in actual) != sorted(exp.name for exp in expected):
            problems.append(
                f"multiset {sorted(c.name for c in actual)} != expected "
                f"{sorted(e.name for e in expected)}"
            )
        else:
            # Pair each expectation with a call that actually satisfies it, not
            # merely the first one sharing its name: two `aggregate_window`
            # expectations differing only by `device_id` would otherwise be
            # paired in whatever order the model happened to emit them.
            remaining = list(actual)
            for exp in expected:
                hit = next(
                    (
                        c
                        for c in remaining
                        if c.name == exp.name and not _args_failures(exp, c)
                    ),
                    None,
                )
                if hit is None:
                    # Nothing satisfies it; report against the first by name so
                    # the message says which argument was wrong.
                    hit = next((c for c in remaining if c.name == exp.name), None)
                    if hit is not None:
                        problems.extend(_args_failures(exp, hit))
                if hit is not None:
                    remaining.remove(hit)

    else:  # ordered — expected calls appear in this relative order; extras allowed
        cursor = 0
        for exp in expected:
            while cursor < len(actual) and actual[cursor].name != exp.name:
                cursor += 1
            if cursor >= len(actual):
                problems.append(f"expected a {exp.name} call, never saw one (in order)")
                break
            problems.extend(_args_failures(exp, actual[cursor]))
            cursor += 1

    return not problems, problems


def grade_rubric(rubric: Rubric, answer: str) -> tuple[bool, list[str]]:
    """Keyword checks on the final prose, case-insensitive."""
    haystack = answer.lower()
    problems: list[str] = []

    for needle in rubric.must_include:
        if needle.lower() not in haystack:
            problems.append(f"answer is missing required text {needle!r}")

    if rubric.must_include_any and not any(
        needle.lower() in haystack for needle in rubric.must_include_any
    ):
        problems.append(f"answer contains none of {list(rubric.must_include_any)}")

    for needle in rubric.must_not_include:
        if needle.lower() in haystack:
            problems.append(f"answer contains forbidden text {needle!r}")

    if not answer.strip():
        problems.append("answer is empty")

    return not problems, problems
