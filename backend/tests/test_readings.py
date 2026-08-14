"""Reading API tests: ingest, query, aggregation, and derived alerts.

Aggregation is asserted against hand-computed values so the tests would catch a
silently-wrong rollup, not merely a crash.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.core.security import create_access_token
from app.models import Device, User

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def auth_header(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def iso(moment: datetime) -> str:
    return moment.isoformat()


# --- POST /devices/{id}/readings --------------------------------------------


async def test_create_reading(authed_client: AsyncClient, device: Device) -> None:
    resp = await authed_client.post(
        f"/devices/{device.id}/readings",
        json={"value": 42.5, "time": iso(BASE)},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["value"] == 42.5
    assert body["device_id"] == str(device.id)
    assert datetime.fromisoformat(body["time"]) == BASE


async def test_create_reading_defaults_time_to_now(
    authed_client: AsyncClient, device: Device
) -> None:
    before = datetime.now(UTC)
    resp = await authed_client.post(f"/devices/{device.id}/readings", json={"value": 1})
    assert resp.status_code == 201, resp.text
    stamped = datetime.fromisoformat(resp.json()["time"])
    assert before <= stamped <= datetime.now(UTC)


async def test_create_reading_treats_naive_time_as_utc(
    authed_client: AsyncClient, device: Device
) -> None:
    """A naive timestamp must not be read against the server's local zone."""
    resp = await authed_client.post(
        f"/devices/{device.id}/readings",
        json={"value": 1.0, "time": "2026-03-01T12:00:00"},
    )
    assert resp.status_code == 201, resp.text
    assert datetime.fromisoformat(resp.json()["time"]) == BASE


async def test_create_reading_converts_offset_time_to_utc(
    authed_client: AsyncClient, device: Device
) -> None:
    resp = await authed_client.post(
        f"/devices/{device.id}/readings",
        json={"value": 1.0, "time": "2026-03-01T14:00:00+02:00"},
    )
    assert resp.status_code == 201, resp.text
    assert datetime.fromisoformat(resp.json()["time"]) == BASE


async def test_duplicate_timestamp_conflicts(
    authed_client: AsyncClient, device: Device
) -> None:
    """(time, device_id) is the primary key, so the same instant twice is a 409."""
    payload = {"value": 1.0, "time": iso(BASE)}
    assert (
        await authed_client.post(f"/devices/{device.id}/readings", json=payload)
    ).status_code == 201
    second = await authed_client.post(f"/devices/{device.id}/readings", json=payload)
    assert second.status_code == 409
    assert second.json()["code"] == "duplicate_reading"


async def test_create_reading_requires_authentication(
    client: AsyncClient, device: Device
) -> None:
    resp = await client.post(f"/devices/{device.id}/readings", json={"value": 1.0})
    assert resp.status_code == 401


async def test_cannot_post_readings_to_another_users_device(
    authed_client: AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    """The device gate is what keeps telemetry isolated — readings have no owner."""
    stranger = await make_user(email="stranger@example.com")
    their_device = await make_device(stranger, name="Not-Yours")

    resp = await authed_client.post(
        f"/devices/{their_device.id}/readings", json={"value": 1.0}
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "device_not_found"


# --- POST /devices/{id}/readings/bulk ---------------------------------------


async def test_bulk_insert(authed_client: AsyncClient, device: Device) -> None:
    rows = [
        {"value": float(i), "time": iso(BASE + timedelta(minutes=i))} for i in range(50)
    ]
    resp = await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"count": 50}

    listed = await authed_client.get("/readings", params={"device_id": str(device.id)})
    assert len(listed.json()) == 50


async def test_bulk_requires_explicit_times(
    authed_client: AsyncClient, device: Device
) -> None:
    """`time` is mandatory in bulk — a whole batch defaulting to now would collide."""
    resp = await authed_client.post(
        f"/devices/{device.id}/readings/bulk", json=[{"value": 1.0}]
    )
    assert resp.status_code == 422
    assert resp.json()["field"] == "time"


async def test_bulk_with_duplicate_timestamps_conflicts(
    authed_client: AsyncClient, device: Device
) -> None:
    rows = [{"value": 1.0, "time": iso(BASE)}, {"value": 2.0, "time": iso(BASE)}]
    resp = await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)
    assert resp.status_code == 409
    assert resp.json()["code"] == "duplicate_reading"


async def test_bulk_empty_list_is_a_noop(
    authed_client: AsyncClient, device: Device
) -> None:
    resp = await authed_client.post(f"/devices/{device.id}/readings/bulk", json=[])
    assert resp.status_code == 201
    assert resp.json() == {"count": 0}


async def test_bulk_rejects_oversized_batch(
    authed_client: AsyncClient, device: Device
) -> None:
    rows = [
        {"value": 1.0, "time": iso(BASE + timedelta(seconds=i))} for i in range(10_001)
    ]
    resp = await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)
    assert resp.status_code == 422


# --- GET /readings ----------------------------------------------------------


async def test_list_readings_newest_first(
    authed_client: AsyncClient, device: Device
) -> None:
    rows = [
        {"value": float(i), "time": iso(BASE + timedelta(hours=i))} for i in range(5)
    ]
    await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)

    resp = await authed_client.get("/readings", params={"device_id": str(device.id)})
    assert resp.status_code == 200
    values = [r["value"] for r in resp.json()]
    assert values == [4.0, 3.0, 2.0, 1.0, 0.0]


async def test_list_readings_respects_limit(
    authed_client: AsyncClient, device: Device
) -> None:
    """A truncating limit must keep the NEWEST rows, not an arbitrary slice."""
    rows = [
        {"value": float(i), "time": iso(BASE + timedelta(hours=i))} for i in range(10)
    ]
    await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)

    resp = await authed_client.get(
        "/readings", params={"device_id": str(device.id), "limit": 3}
    )
    assert [r["value"] for r in resp.json()] == [9.0, 8.0, 7.0]


async def test_list_readings_window_is_half_open(
    authed_client: AsyncClient, device: Device
) -> None:
    """[start, end): the row exactly at `end` is excluded, the one at `start` kept."""
    rows = [
        {"value": float(i), "time": iso(BASE + timedelta(hours=i))} for i in range(4)
    ]
    await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)

    resp = await authed_client.get(
        "/readings",
        params={
            "device_id": str(device.id),
            "start": iso(BASE + timedelta(hours=1)),
            "end": iso(BASE + timedelta(hours=3)),
        },
    )
    assert sorted(r["value"] for r in resp.json()) == [1.0, 2.0]


async def test_list_readings_rejects_inverted_window(
    authed_client: AsyncClient, device: Device
) -> None:
    resp = await authed_client.get(
        "/readings",
        params={
            "device_id": str(device.id),
            "start": iso(BASE + timedelta(hours=3)),
            "end": iso(BASE),
        },
    )
    assert resp.status_code == 422


async def test_list_readings_rejects_excessive_limit(
    authed_client: AsyncClient, device: Device
) -> None:
    resp = await authed_client.get(
        "/readings", params={"device_id": str(device.id), "limit": 10_001}
    )
    assert resp.status_code == 422


async def test_list_readings_for_another_users_device_is_404(
    authed_client: AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    stranger = await make_user(email="stranger@example.com")
    their_device = await make_device(stranger, name="Not-Yours")

    resp = await authed_client.get(
        "/readings", params={"device_id": str(their_device.id)}
    )
    assert resp.status_code == 404


async def test_list_readings_requires_authentication(
    client: AsyncClient, device: Device
) -> None:
    resp = await client.get("/readings", params={"device_id": str(device.id)})
    assert resp.status_code == 401


# --- GET /readings/aggregate ------------------------------------------------


async def test_aggregate_avg_buckets_by_hour(
    authed_client: AsyncClient, device: Device
) -> None:
    """Two hours of data, hand-computed means."""
    rows = [
        # Hour 12: 10, 20, 30 -> avg 20
        {"value": 10.0, "time": iso(BASE)},
        {"value": 20.0, "time": iso(BASE + timedelta(minutes=20))},
        {"value": 30.0, "time": iso(BASE + timedelta(minutes=40))},
        # Hour 13: 100, 200 -> avg 150
        {"value": 100.0, "time": iso(BASE + timedelta(hours=1))},
        {"value": 200.0, "time": iso(BASE + timedelta(hours=1, minutes=30))},
    ]
    await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)

    resp = await authed_client.get(
        "/readings/aggregate",
        params={"device_id": str(device.id), "window": "1h", "fn": "avg"},
    )
    assert resp.status_code == 200, resp.text
    buckets = resp.json()
    assert [b["value"] for b in buckets] == [20.0, 150.0]
    # Buckets are floored to the hour and returned oldest-first.
    assert datetime.fromisoformat(buckets[0]["bucket"]) == BASE
    assert datetime.fromisoformat(buckets[1]["bucket"]) == BASE + timedelta(hours=1)


async def test_aggregate_min_max(authed_client: AsyncClient, device: Device) -> None:
    rows = [
        {"value": 5.0, "time": iso(BASE)},
        {"value": 15.0, "time": iso(BASE + timedelta(minutes=10))},
        {"value": -3.0, "time": iso(BASE + timedelta(minutes=20))},
    ]
    await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)

    params = {"device_id": str(device.id), "window": "1h"}
    low = await authed_client.get("/readings/aggregate", params={**params, "fn": "min"})
    high = await authed_client.get(
        "/readings/aggregate", params={**params, "fn": "max"}
    )
    assert [b["value"] for b in low.json()] == [-3.0]
    assert [b["value"] for b in high.json()] == [15.0]


async def test_aggregate_p95(authed_client: AsyncClient, device: Device) -> None:
    """percentile_cont(0.95) over 1..100 interpolates to 95.05."""
    rows = [
        {"value": float(i), "time": iso(BASE + timedelta(seconds=i))}
        for i in range(1, 101)
    ]
    await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)

    resp = await authed_client.get(
        "/readings/aggregate",
        params={"device_id": str(device.id), "window": "1h", "fn": "p95"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["value"] == 95.05


async def test_aggregate_daily_window(
    authed_client: AsyncClient, device: Device
) -> None:
    rows = [
        {"value": 1.0, "time": iso(BASE)},
        {"value": 3.0, "time": iso(BASE + timedelta(hours=6))},
        {"value": 10.0, "time": iso(BASE + timedelta(days=1))},
    ]
    await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)

    resp = await authed_client.get(
        "/readings/aggregate",
        params={"device_id": str(device.id), "window": "1d", "fn": "avg"},
    )
    assert [b["value"] for b in resp.json()] == [2.0, 10.0]


async def test_aggregate_honours_the_time_window(
    authed_client: AsyncClient, device: Device
) -> None:
    rows = [
        {"value": float(i), "time": iso(BASE + timedelta(hours=i))} for i in range(4)
    ]
    await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)

    resp = await authed_client.get(
        "/readings/aggregate",
        params={
            "device_id": str(device.id),
            "window": "1h",
            "fn": "avg",
            "start": iso(BASE + timedelta(hours=1)),
            "end": iso(BASE + timedelta(hours=3)),
        },
    )
    assert [b["value"] for b in resp.json()] == [1.0, 2.0]


async def test_aggregate_rejects_unknown_window_or_fn(
    authed_client: AsyncClient, device: Device
) -> None:
    base = {"device_id": str(device.id), "window": "1h", "fn": "avg"}
    bad_window = await authed_client.get(
        "/readings/aggregate", params={**base, "window": "1m"}
    )
    bad_fn = await authed_client.get(
        "/readings/aggregate", params={**base, "fn": "median"}
    )
    assert bad_window.status_code == 422
    assert bad_fn.status_code == 422


async def test_aggregate_empty_range_returns_empty_list(
    authed_client: AsyncClient, device: Device
) -> None:
    resp = await authed_client.get(
        "/readings/aggregate",
        params={"device_id": str(device.id), "window": "1h", "fn": "avg"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


async def test_aggregate_for_another_users_device_is_404(
    authed_client: AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    stranger = await make_user(email="stranger@example.com")
    their_device = await make_device(stranger, name="Not-Yours")

    resp = await authed_client.get(
        "/readings/aggregate",
        params={"device_id": str(their_device.id), "window": "1h", "fn": "avg"},
    )
    assert resp.status_code == 404


# --- GET /readings/alerts ---------------------------------------------------


async def test_alerts_reports_threshold_breaches(
    authed_client: AsyncClient,
    make_device: Callable[..., Awaitable[Device]],
    user: User,
) -> None:
    device = await make_device(
        user, name="Bounded", min_threshold=10.0, max_threshold=90.0
    )
    rows = [
        {"value": 50.0, "time": iso(BASE)},  # in band
        {"value": 5.0, "time": iso(BASE + timedelta(minutes=1))},  # under min
        {"value": 95.0, "time": iso(BASE + timedelta(minutes=2))},  # over max
    ]
    await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)

    resp = await authed_client.get(
        "/readings/alerts", params={"since": iso(BASE - timedelta(hours=1))}
    )
    assert resp.status_code == 200, resp.text
    alerts = resp.json()
    assert len(alerts) == 2

    # Newest first.
    assert alerts[0]["value"] == 95.0
    assert alerts[0]["bound"] == "max"
    assert alerts[0]["threshold"] == 90.0
    assert alerts[1]["value"] == 5.0
    assert alerts[1]["bound"] == "min"
    assert alerts[1]["threshold"] == 10.0
    # Self-describing, so the agent can cite the device by name.
    assert alerts[0]["device_name"] == "Bounded"
    assert alerts[0]["unit"] == "L/min"


async def test_devices_without_thresholds_never_alert(
    authed_client: AsyncClient, device: Device
) -> None:
    """Both bounds NULL means nothing can breach."""
    await authed_client.post(
        f"/devices/{device.id}/readings/bulk",
        json=[{"value": 1e9, "time": iso(BASE)}],
    )
    resp = await authed_client.get(
        "/readings/alerts", params={"since": iso(BASE - timedelta(hours=1))}
    )
    assert resp.json() == []


async def test_alerts_respects_since(
    authed_client: AsyncClient,
    make_device: Callable[..., Awaitable[Device]],
    user: User,
) -> None:
    device = await make_device(user, name="Bounded", max_threshold=10.0)
    rows = [
        {"value": 99.0, "time": iso(BASE)},
        {"value": 98.0, "time": iso(BASE + timedelta(hours=2))},
    ]
    await authed_client.post(f"/devices/{device.id}/readings/bulk", json=rows)

    resp = await authed_client.get(
        "/readings/alerts", params={"since": iso(BASE + timedelta(hours=1))}
    )
    assert [a["value"] for a in resp.json()] == [98.0]


async def test_alerts_can_filter_to_one_device(
    authed_client: AsyncClient,
    make_device: Callable[..., Awaitable[Device]],
    user: User,
) -> None:
    a = await make_device(user, name="A", max_threshold=10.0)
    b = await make_device(user, name="B", max_threshold=10.0)
    for target in (a, b):
        await authed_client.post(
            f"/devices/{target.id}/readings",
            json={"value": 99.0, "time": iso(BASE)},
        )

    everything = await authed_client.get(
        "/readings/alerts", params={"since": iso(BASE - timedelta(hours=1))}
    )
    just_a = await authed_client.get(
        "/readings/alerts",
        params={"since": iso(BASE - timedelta(hours=1)), "device_id": str(a.id)},
    )
    assert len(everything.json()) == 2
    assert [alert["device_name"] for alert in just_a.json()] == ["A"]


async def test_alerts_never_include_another_users_devices(
    authed_client: AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
    make_reading: Callable[..., Awaitable[object]],
) -> None:
    """The un-filtered alerts query spans devices, so its scoping matters most."""
    stranger = await make_user(email="stranger@example.com")
    their_device = await make_device(stranger, name="Theirs", max_threshold=1.0)
    await make_reading(their_device, value=999.0, time=BASE)

    resp = await authed_client.get(
        "/readings/alerts", params={"since": iso(BASE - timedelta(hours=1))}
    )
    assert resp.json() == []


async def test_alerts_for_another_users_device_is_404(
    authed_client: AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    stranger = await make_user(email="stranger@example.com")
    their_device = await make_device(stranger, name="Theirs", max_threshold=1.0)

    resp = await authed_client.get(
        "/readings/alerts",
        params={"since": iso(BASE), "device_id": str(their_device.id)},
    )
    assert resp.status_code == 404


async def test_alerts_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/readings/alerts", params={"since": iso(BASE)})
    assert resp.status_code == 401


async def test_alerts_requires_since(authed_client: AsyncClient) -> None:
    resp = await authed_client.get("/readings/alerts")
    assert resp.status_code == 422
    assert resp.json()["field"] == "since"


# --- Cross-cutting ----------------------------------------------------------


async def test_unknown_device_id_is_404_everywhere(authed_client: AsyncClient) -> None:
    ghost = uuid.uuid4()
    post = await authed_client.post(f"/devices/{ghost}/readings", json={"value": 1.0})
    listed = await authed_client.get("/readings", params={"device_id": str(ghost)})
    agg = await authed_client.get(
        "/readings/aggregate",
        params={"device_id": str(ghost), "window": "1h", "fn": "avg"},
    )
    assert post.status_code == listed.status_code == agg.status_code == 404
