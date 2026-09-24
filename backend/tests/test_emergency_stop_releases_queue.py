"""An emergency stop must not leave accounts labelled as waiting.

Measured 24/09/2026: a stop dropped 34 waiting tasks, and all 34 accounts
kept the QUEUED label in the list. The screen read "waiting its turn" for a
queue that no longer existed, which is indistinguishable from a dispatcher
that has stalled - and that is exactly how it was reported.
"""
import asyncio

import pytest

from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher


def _dispatcher_with_queue(payloads):
    dispatcher = ConcurrentTaskDispatcher.__new__(ConcurrentTaskDispatcher)
    dispatcher._emergency_stop_generation = 0
    dispatcher.global_pause_event = asyncio.Event()
    dispatcher.is_globally_paused = False
    dispatcher.account_pause_events = {}
    dispatcher.paused_account_ids = set()
    dispatcher.active_tasks = {}
    dispatcher._pending_accounts = set()
    dispatcher.queue = asyncio.Queue()
    for payload in payloads:
        dispatcher.queue.put_nowait(payload)

    async def no_broadcast():
        return None

    # The stop also tells the dashboard where things stand; that part has its
    # own wiring and is not what these tests are about.
    dispatcher.broadcast_global_state = no_broadcast
    return dispatcher


def test_dropped_tasks_release_their_accounts():
    released = []

    async def scenario():
        dispatcher = _dispatcher_with_queue([
            {"account_id": "one@hotmail.com", "task_type": "LOGIN_CREDENTIAL"},
            {"account_id": "two@hotmail.com", "task_type": "LOGIN_CREDENTIAL"},
        ])

        async def fake_update(account_id, status, step_desc="IDLE", **_kwargs):
            released.append((account_id, status, step_desc))

        dispatcher._update_account_status = fake_update
        await dispatcher.emergency_stop_all()

    asyncio.run(scenario())

    assert [row[0] for row in released] == ["one@hotmail.com", "two@hotmail.com"]
    assert {row[1] for row in released} == {"IDLE"}
    assert all("Dừng khẩn cấp" in row[2] for row in released)


def test_a_stop_with_an_empty_queue_touches_nothing():
    released = []

    async def scenario():
        dispatcher = _dispatcher_with_queue([])

        async def fake_update(account_id, status, step_desc="IDLE", **_kwargs):
            released.append(account_id)

        dispatcher._update_account_status = fake_update
        await dispatcher.emergency_stop_all()

    asyncio.run(scenario())
    assert released == []


def test_a_row_that_cannot_be_written_does_not_stop_the_rest():
    released = []

    async def scenario():
        dispatcher = _dispatcher_with_queue([
            {"account_id": "broken@hotmail.com", "task_type": "LOGIN_CREDENTIAL"},
            {"account_id": "fine@hotmail.com", "task_type": "LOGIN_CREDENTIAL"},
        ])

        async def fake_update(account_id, status, step_desc="IDLE", **_kwargs):
            if account_id == "broken@hotmail.com":
                raise RuntimeError("database is locked")
            released.append(account_id)

        dispatcher._update_account_status = fake_update
        await dispatcher.emergency_stop_all()

    asyncio.run(scenario())
    assert released == ["fine@hotmail.com"]
