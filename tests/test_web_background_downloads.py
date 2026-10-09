"""Regression tests for background web downloads."""

import asyncio
import logging
from unittest.mock import Mock

from spotdl.utils.web import Client, app_state
from spotdl.web import routes
from spotdl.web.utils import Signals


def make_client():
    client = Client.__new__(Client)
    client.client_id = "background-test"
    client.download_tasks = set()
    client.disconnect_timer = None
    return client


async def test_request_cancellation_does_not_cancel_download(monkeypatch):
    client = make_client()
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    async def fake_download(signals):
        started.set()
        await release.wait()
        completed.set()
        yield "finished"

    monkeypatch.setattr(routes, "gen_download", fake_download)
    monkeypatch.setattr(routes.Client, "get_instance", lambda _: client)
    monkeypatch.setattr(app_state, "logger", logging.getLogger("test"), raising=False)

    async def request():
        signals = Signals()
        signals.client_id = client.client_id
        assert routes.start_background_download(signals)
        await asyncio.Event().wait()

    request_task = asyncio.create_task(request())
    await asyncio.wait_for(started.wait(), timeout=1)
    download_task = next(iter(client.download_tasks))

    request_task.cancel()
    await asyncio.gather(request_task, return_exceptions=True)
    assert not download_task.cancelled()

    release.set()
    await asyncio.wait_for(download_task, timeout=1)
    await asyncio.sleep(0)
    assert completed.is_set()
    assert not client.download_tasks


async def test_disconnect_retains_client_with_running_download(monkeypatch):
    client = make_client()
    release = asyncio.Event()
    task = client.start_download(release.wait())
    clients = {client.client_id: client}
    timer = Mock()

    monkeypatch.setattr(app_state, "clients", clients)
    monkeypatch.setattr(app_state, "logger", logging.getLogger("test"), raising=False)
    monkeypatch.setattr("spotdl.utils.web.threading.Timer", Mock(return_value=timer))

    try:
        client.disconnect_now()
        assert clients[client.client_id] is client
        timer.start.assert_called_once()
    finally:
        release.set()
        await task
        await asyncio.sleep(0)

    client.disconnect_now()
    assert client.client_id not in clients


async def test_background_failure_is_retrieved_and_logged(monkeypatch):
    client = make_client()
    logger = Mock()
    monkeypatch.setattr(app_state, "logger", logger, raising=False)

    async def fail():
        raise RuntimeError("Expected failure")

    task = client.start_download(fail())
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert not client.download_tasks
    logger.error.assert_called_once()
