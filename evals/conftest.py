"""Eval-suite wiring: import paths, run mode, and the metrics report.

`evals/` sits outside `backend/`, so nothing here is importable by default. The
path setup below is what lets `pytest evals/` work from the repo root whether or
not the backend is pip-installed.

The reporting hooks collect one `CaseResult` per case and print the metrics
SPEC § Eval plan asks for — tool-call precision, rubric pass rate, p50/p95
latency, mean cost, prompt version — plus write them to `evals/results/latest.json`
so a CI job or a weekly drift run has something machine-readable to diff.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"

# Repo root first so `evals.*` resolves as a package; backend second so
# `app.*` resolves without an editable install.
for entry in (REPO_ROOT, BACKEND):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

# `app.agent.tools` reads settings at import time, and `SECRET_KEY` has no
# default by design (see core/config.py). The eval suite never mints or verifies
# a token, so that is incidental coupling through the settings singleton — not a
# real requirement. Supplying a throwaway value keeps `pytest evals/` working on
# a fresh clone with no `.env`, instead of failing during collection.
#
# Set before any `app.*` import below, because `get_settings()` is cached.
os.environ.setdefault("SECRET_KEY", "eval-suite-only-never-used-to-sign-anything")

if TYPE_CHECKING:
    from evals.grading import CaseResult

RESULTS_DIR = REPO_ROOT / "evals" / "results"

# Populated by the runner via `record_result`, drained by the summary hook.
_RESULTS: list[CaseResult] = []


def record_result(result: CaseResult) -> None:
    """Called once per graded case."""
    _RESULTS.append(result)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--eval-report",
        action="store",
        default=str(RESULTS_DIR / "latest.json"),
        help="Where to write the eval metrics JSON.",
    )


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(int(fraction * len(ordered)), len(ordered) - 1)
    return ordered[index]


def _summarize() -> dict[str, Any]:
    from evals.cassette import current_mode

    total = len(_RESULTS)
    latencies = [r.latency_ms for r in _RESULTS]
    costs = [r.cost_usd for r in _RESULTS]
    by_category: dict[str, dict[str, int]] = {}
    for result in _RESULTS:
        bucket = by_category.setdefault(result.case.category, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(result.passed)

    versions = Counter(r.prompt_version for r in _RESULTS)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": current_mode(),
        "cases_graded": total,
        "passed": sum(r.passed for r in _RESULTS),
        # SPEC's "tool-call precision (correct sequence)" and "rubric pass rate".
        "tool_call_precision": (
            round(sum(r.tools_ok for r in _RESULTS) / total, 4) if total else 0.0
        ),
        "rubric_pass_rate": (
            round(sum(r.rubric_ok for r in _RESULTS) / total, 4) if total else 0.0
        ),
        "latency_ms_p50": _percentile(latencies, 0.50),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "mean_cost_usd": round(statistics.fmean(costs), 6) if costs else 0.0,
        "total_cost_usd": round(sum(costs), 6),
        "prompt_versions": dict(versions),
        "by_category": by_category,
        "failures": [
            {
                "id": r.case.id,
                "category": r.case.category,
                "tools": r.tool_failures,
                "rubric": r.rubric_failures,
                "errors": r.errors,
            }
            for r in _RESULTS
            if not r.passed
        ],
    }


def pytest_terminal_summary(
    terminalreporter: Any, exitstatus: int, config: pytest.Config
) -> None:
    """Print the metrics table and write the JSON report."""
    if not _RESULTS:
        return

    report = _summarize()
    destination = Path(config.getoption("--eval-report"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")

    write = terminalreporter.write_line
    write("")
    write("eval metrics " + "-" * 54)
    write(f"  mode                 {report['mode']}")
    write(f"  cases graded         {report['cases_graded']}")
    write(f"  passed               {report['passed']}/{report['cases_graded']}")
    write(f"  tool-call precision  {report['tool_call_precision']:.0%}")
    write(f"  rubric pass rate     {report['rubric_pass_rate']:.0%}")
    write(
        f"  latency p50 / p95    {report['latency_ms_p50']}ms / {report['latency_ms_p95']}ms"
    )
    write(f"  mean cost / query    ${report['mean_cost_usd']:.6f}")
    write(f"  total cost           ${report['total_cost_usd']:.6f}")
    write(f"  prompt version(s)    {report['prompt_versions']}")
    for category, counts in sorted(report["by_category"].items()):
        write(f"    {category:<16} {counts['passed']}/{counts['total']}")
    write(f"  report               {destination}")
    write("-" * 67)
