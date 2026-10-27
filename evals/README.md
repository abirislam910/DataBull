# Agent evals

Thirty labeled cases that measure whether the AI operator's assistant calls the
right tools and grounds its answers. See `/docs/SPEC.md` § Eval plan for the
contract this implements.

These are **not** the agent's unit tests. `backend/tests/test_agent.py` proves the
tool loop works; this suite asks whether the *model* behaves, which is a different
question with a different failure mode.

## Running

```bash
pytest evals/                      # replay recorded turns: free, deterministic, what CI runs
EVAL_MODE=record pytest evals/     # re-record from the live API (costs money)
EVAL_MODE=live   pytest evals/     # call the live API, write nothing; for tracking drift
```

Run from the repo root. Replay mode needs no `.env` and no `ANTHROPIC_API_KEY`.

## Metrics

Every run ends with a metrics block, and writes the same numbers to
`evals/results/latest.json` (gitignored) so runs can be compared over time:

```
eval metrics ------------------------------------------------------
  mode                 replay
  cases graded         30
  passed               30/30
  tool-call precision  100%
  rubric pass rate     100%
  latency p50 / p95    0ms / 0ms
  mean cost / query    $0.006409
  total cost           $0.192264
  prompt version(s)    {'v1': 30}
    aggregation      8/8
    alert_aware      3/3
    multi_step       5/5
    should_decline   2/2
    simple_recall    12/12
```

In replay mode, cost reflects the token counts recorded in each cassette, and
latency is near zero because no model is called. Record or live mode reports real
latency.

**A case with no cassette is skipped, not failed**, so a newly added case does not
turn the suite red before it has been recorded. `cases graded` shows how many cases
actually ran, so a run where everything was skipped cannot be mistaken for a pass.

## Recording cassettes

Recording calls the real Anthropic API once per case and **costs real money**. The
full set of thirty at `claude-sonnet-5` with `AGENT_EFFORT=low` costs about $0.19.
Results are not deterministic between recordings.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
EVAL_MODE=record pytest evals/ -k recall-list-devices    # one case
EVAL_MODE=record pytest evals/                            # all thirty
```

Cassettes **are** committed: they are the fixtures CI replays. Review them like
code. A diff in a cassette is a change in model behavior, which is exactly what
this suite exists to notice.

Re-record when the system prompt changes, the tool schemas change, or the model
changes. Each cassette records the `model` and `prompt_version` it was captured
under, so a stale one is easy to identify.

## Files

| File | What it holds |
|:-----|:--------------|
| `cases.yaml` | The 30 cases: question, expected tool sequence, rubric |
| `runner.py` | The pytest module: builds `AgentServices`, runs `run_agent`, grades |
| `grading.py` | Case schema, the YAML loader, and both graders |
| `fixtures.py` | The deterministic fleet and in-memory repositories |
| `cassette.py` | Record and replay of Anthropic turns |
| `conftest.py` | Import paths, run mode, metrics report |
| `test_harness.py` | Tests for everything above |
| `cassettes/` | One recorded conversation per case |

## Writing a case

```yaml
- id: agg-pump3-avg-today
  category: aggregation
  question: What is the average pressure on Pump-3 today?
  match: exact            # exact | ordered | set   (default exact)
  max_tools: 4            # optional ceiling on total calls
  forbid_tools: []        # tools that must NOT be called
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
    must_match: ['(?i)\baverage\b']     # regexes, all must match
```

Arguments are matched as **patterns, not values**. A case checks that `window` was
`1h` and that the device was Pump-3, not which exact timestamp the model chose for
`start`.

The distribution across categories is fixed by the eval plan (12 simple recall,
8 aggregation, 5 multi-step, 3 alert-aware, 2 should-decline), and
`test_case_file_matches_spec` fails if `cases.yaml` drifts from it.

### Grading behavior, not wording

A substring list is a brittle way to grade prose: it fails correct answers that
chose different words. Two sturdier options:

**`forbid_tools` grades behavior.** For an unknown device, the decisive question is
not what words the model chose but whether it invented a UUID and queried it. There
is no valid `device_id` for a device that does not exist, so forbidding the read
tools asserts the real property under any phrasing.

**`must_match` replaces a long substring list with one regex.** A single
alternation covers the phrasing space. Patterns are matched against the original
text, so `(?i)` is opt-in, and a broken pattern fails when the cases load rather
than during grading.

Prefer grading behavior. Where only prose can carry the signal, prefer `must_match`
over a growing `must_include_any` list.

### Three things worth knowing

**Nearly every sequence opens with `list_devices`.** The other three tools take a
`device_id` UUID, and `list_devices` is the only place the model can get one. That
is a property of the tool surface, not of phrasing.

**An unknown rubric key is an error, not a no-op.** `must_includ: [...]` would
otherwise check nothing and pass silently, so the loader rejects it.

**`ordered` tolerates fan-out.** A model answering "compare A and B" may make
several calls per device. Both `ordered` and `set` pair each expectation with a
call that satisfies its *arguments*, not merely the next one sharing its name. Use
`exact` only when you mean "these calls and no others".

## Determinism

Two properties hold the suite together:

- **Device UUIDs are stable.** They are `uuid5` values derived from the device
  name, so a recorded `device_id` is still valid on replay. Random UUIDs would
  invalidate every cassette as soon as it was written.
- **"Now" is pinned** to `2026-03-01T12:00Z` with seven days of history behind it,
  so "the last 24 hours" resolves to the same absolute window on every run.
