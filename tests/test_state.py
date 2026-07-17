from servermon.checker import CheckResult
from servermon.state import ErrorState, LastError


def make_result(success, detail, checked_at="Fri, 17 Jul 2026 08:00:00 -0400"):
    return CheckResult(
        url="https://example.com",
        status_code=200 if success else 500,
        success=success,
        detail=detail,
        response_time_ms=1,
        match_found=None,
        checked_at=checked_at,
        server="",
    )


def test_never_failed_returns_none():
    state = ErrorState()
    assert state.record("dev1", make_result(True, "OK: HTTP 200 OK (1 ms)")) is None


def test_failure_is_recorded_and_returned():
    state = ErrorState()
    failed = make_result(False, "FAILED: HTTP 500 (1 ms)")
    assert state.record("dev1", failed) == LastError(failed.detail, failed.checked_at)


def test_success_keeps_previous_error():
    state = ErrorState()
    failed = make_result(False, "FAILED: HTTP 500 (1 ms)")
    state.record("dev1", failed)

    ok = make_result(True, "OK: HTTP 200 OK (1 ms)")
    assert state.record("dev1", ok) == LastError(failed.detail, failed.checked_at)


def test_new_error_replaces_old_error():
    state = ErrorState()
    state.record("dev1", make_result(False, "FAILED: HTTP 500 (1 ms)"))
    newer = make_result(False, "ERROR: no HTTP response: refused")
    assert state.record("dev1", newer) == LastError(newer.detail, newer.checked_at)


def test_devices_are_independent():
    state = ErrorState()
    state.record("dev1", make_result(False, "FAILED: HTTP 500 (1 ms)"))
    assert state.record("dev2", make_result(True, "OK: HTTP 200 OK (1 ms)")) is None


def test_state_survives_reload(tmp_path):
    path = tmp_path / "state.json"
    failed = make_result(False, "FAILED: HTTP 503 (2 ms)")

    first = ErrorState(path)
    first.record("dev1", failed)
    first.save()

    second = ErrorState(path)
    ok = make_result(True, "OK: HTTP 200 OK (1 ms)")
    assert second.record("dev1", ok) == LastError(failed.detail, failed.checked_at)


def test_corrupt_state_file_starts_fresh(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")

    state = ErrorState(path)  # must not raise
    assert state.record("dev1", make_result(True, "OK: HTTP 200 OK (1 ms)")) is None
    state.save()  # must be able to overwrite the corrupt file


def test_save_without_path_is_noop():
    ErrorState().save()  # must not raise
