from app import main


class _FakeLoop:
    def __init__(self) -> None:
        self.forwarded = []

    def default_exception_handler(self, context: dict) -> None:
        self.forwarded.append(context)


def test_ignores_only_windows_proactor_close_reset(monkeypatch):
    loop = _FakeLoop()
    error = ConnectionResetError(10054, "connection reset", None, 10054)
    context = {
        "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)",
        "handle": "<Handle _ProactorBasePipeTransport._call_connection_lost(None)>",
        "exception": error,
    }
    monkeypatch.setattr(main.sys, "platform", "win32")

    main._windows_loop_exception_handler(loop, context)

    assert loop.forwarded == []


def test_forwards_unrelated_connection_reset(monkeypatch):
    loop = _FakeLoop()
    context = {
        "message": "Unhandled task exception",
        "exception": ConnectionResetError(10054, "connection reset", None, 10054),
    }
    monkeypatch.setattr(main.sys, "platform", "win32")

    main._windows_loop_exception_handler(loop, context)

    assert loop.forwarded == [context]
