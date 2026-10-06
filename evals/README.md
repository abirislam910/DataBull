# Agent evals

Thirty labeled cases that measure whether the AI operator's assistant calls the
right tools and grounds its answers. See `/docs/SPEC.md` § Eval plan for the
contract this implements.

These tests are **not** the agent's unit tests. `backend/tests/test_agent.py`
proves the tool loop works; this suite asks whether the *model* behaves, which is
a different question with a different failure mode.

## Running

```bash
pytest evals/                      # replay recorded turns — free, deterministic, CI's mode
EVAL_MODE=record pytest evals/     # re-record from the live API — SPENDS MONEY
EVAL_MODE=live   pytest evals/     # live, write nothing — for the drift job
```

Run from the repo root. No `.env` and no `ANTHROPIC_API_KEY` are needed in replay
mode.

## Why cases skip

In replay mode a case with no cassette in `evals/cassettes/` is **skipped**, not
failed — a repo that has never recorded should not report thirty red tests. The
metrics block at the end of the run prints `cases graded`, so an all-skipped run
is visible rather than looking like a pass:

```
eval metrics ------------------------------------------------------
  mode                 replay
  cases graded         1
  passed               1/1
  tool-call precision  100%
  rubric pass rate     100%
  latency p50 / p95    0ms / 0ms
  mean cost / query    $0.000800
```

The same numbers land in `evals/results/latest.json` (gitignored) for a CI job or
a drift run to diff.

## Recording cassettes

Recording calls the real Anthropic API once per case and **costs real money**.
Thirty cases at `claude-sonnet-5` with `AGENT_EFFORT=low` is cents, not dollars,
but it is not free and it is not deterministic.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
EVAL_MODE=record pytest evals/ -k recall-list-devices    # one case
EVAL_MODE=record pytest evals/                            # all thirty
```

Cassettes **are** committed — they are the fixtures CI replays. Review them like
code: a diff in a cassette is a change in model behaviour, which is exactly the
thing this suite exists to notice.

Re-record when the system prompt changes, the tool schemas change, or you move to
a different model. A cassette carries the `model` and `prompt_version` it was
taped under so a stale one is identifiable.

## Files

| File | What it holds |
|:-----|:--------------|
| `cases.yaml` | The 30 cases: question, expected tool sequence, rubric |
| `runner.py` | The pytest module — builds `AgentServices`, runs `run_agent`, grades |
| `grading.py` | Case schema, the YAML loader, and both graders |
| `fixtures.py` | The deterministic fleet and in-memory repositories |
| `cassette.py` | Record/replay of Anthropic turns |
| `conftest.py` | Import paths, run mode, metrics report |
| `test_harness.py` | Tests for everything above — the harness's own safety net |

## Writing a case

```yaml
- id: agg-pump3-avg-today
  category: aggregation
  question: What is the average pressure on Pump-3 today?
  match: exact            # exact | ordered | set   (default exact)
  max_tools: 4            # optional ceiling
  expect_tools:
    - name: list_devices
    - name: aggregate_window
      args:
        device_id: device:Pump-3   # the fixture UUID for that device
        fn: avg                    # case-insensitive equality
        start: "re:2026-03-01"     # regex against str(value)
        end: "*"                   # present, any value
  rubric:
    must_include: [Pump-3]              # all of these
    must_include_any: [bar, average]    # at least one
    must_not_include: [will fail]       # none
```

Arguments are matched as **patterns, not values** (SPEC's wording). The eval cares
that `window` was `1h` and that the device was Pump-3 — not which exact timestamp
the model picked for `start`.

Two things worth knowing when writing a case:

**Nearly every sequence opens with `list_devices`.** The other three tools take a
`device_id` UUID, and `list_devices` is the only place the model can get one. That
is a property of the tool surface, not of phrasing.

**An unknown rubric key is an error, not a no-op.** `must_includ: [...]` would
otherwise check nothing and pass silently, so the loader rejects it.

## Determinism

Two properties hold the suite together:

- **Device UUIDs are stable** — `uuid5` from the device name, so a recorded
  `device_id` is still valid on replay. `uuid4` would invalidate every cassette
  the moment it was written.
- **"Now" is pinned** to `2026-03-01T12:00Z` with seven days of history behind it,
  so "the last 24 hours" resolves to the same absolute window on every run.
