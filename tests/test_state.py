import json

from servermon.checker import CheckResult
from servermon.state import DeviceState, LastError


def make_result(
    success,
    detail,
    checked_at="Fri, 17 Jul 2026 08:00:00 -0400",
    status_code=None,
):
    if status_code is None:
        status_code = 200 if success else 500
    return CheckResult(
        url="https://example.com",
        status_code=status_code,
        success=success,
        detail=detail,
        response_time_ms=1,
        match_found=None,
        checked_at=checked_at,
        server="",
    )


def test_never_failed_has_no_last_error():
    state = DeviceState()
    record = state.record("dev1", make_result(True, "OK: HTTP 200 OK (1 ms)"))
    assert record.last_error is None


def test_failure_is_recorded_and_returned():
    state = DeviceState()
    failed = make_result(False, "FAILED: HTTP 500 (1 ms)")
    record = state.record("dev1", failed)
    assert record.last_error == LastError(failed.detail, failed.checked_at)


def test_success_keeps_previous_error():
    state = DeviceState()
    failed = make_result(False, "FAILED: HTTP 500 (1 ms)")
    state.record("dev1", failed)

    ok = make_result(True, "OK: HTTP 200 OK (1 ms)")
    record = state.record("dev1", ok)
    assert record.last_error == LastError(failed.detail, failed.checked_at)


def test_new_error_replaces_old_error():
    state = DeviceState()
    state.record("dev1", make_result(False, "FAILED: HTTP 500 (1 ms)"))
    newer = make_result(False, "ERROR: no HTTP response: refused")
    record = state.record("dev1", newer)
    assert record.last_error == LastError(newer.detail, newer.checked_at)


def test_devices_are_independent():
    state = DeviceState()
    state.record("dev1", make_result(False, "FAILED: HTTP 500 (1 ms)"))
    record = state.record("dev2", make_result(True, "OK: HTTP 200 OK (1 ms)"))
    assert record.last_error is None
    assert record.last_contact is not None


def test_http_response_counts_as_contact():
    state = DeviceState()
    ok = make_result(True, "OK: HTTP 200 OK (1 ms)")
    assert state.record("dev1", ok).last_contact == ok.checked_at


def test_error_response_also_counts_as_contact():
    # A 500 is still the URL answering: the web server communicated.
    state = DeviceState()
    failed = make_result(False, "FAILED: HTTP 500 (1 ms)")
    assert state.record("dev1", failed).last_contact == failed.checked_at


def test_no_response_does_not_update_contact():
    state = DeviceState()
    ok = make_result(True, "OK: HTTP 200 OK (1 ms)")
    state.record("dev1", ok)

    dead = make_result(
        False,
        "ERROR: no HTTP response: refused",
        checked_at="Fri, 17 Jul 2026 09:00:00 -0400",
        status_code=0,
    )
    record = state.record("dev1", dead)
    # Contact stays at the last real HTTP response; the error is current.
    assert record.last_contact == ok.checked_at
    assert record.last_error == LastError(dead.detail, dead.checked_at)


def test_never_contacted_has_no_contact():
    state = DeviceState()
    dead = make_result(False, "ERROR: no HTTP response: refused", status_code=0)
    assert state.record("dev1", dead).last_contact is None


def test_state_survives_reload(tmp_path):
    path = tmp_path / "state.json"
    failed = make_result(False, "FAILED: HTTP 503 (2 ms)")

    first = DeviceState(path)
    first.record("dev1", failed)
    first.save()

    second = DeviceState(path)
    ok = make_result(
        True, "OK: HTTP 200 OK (1 ms)", checked_at="Fri, 17 Jul 2026 09:00:00 -0400"
    )
    record = second.record("dev1", ok)
    assert record.last_error == LastError(failed.detail, failed.checked_at)
    assert record.last_contact == ok.checked_at


def test_migrates_pre_02_state_format(tmp_path):
    # Older versions stored the last error directly as the device entry.
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {"dev1": {"detail": "FAILED: HTTP 500 (1 ms)", "time": "old-time"}}
        ),
        encoding="utf-8",
    )

    state = DeviceState(path)
    record = state.record("dev1", make_result(True, "OK: HTTP 200 OK (1 ms)"))
    assert record.last_error == LastError("FAILED: HTTP 500 (1 ms)", "old-time")


def test_concurrent_instances_merge_on_save(tmp_path):
    """Two plugin instances saving must not roll back each other's data
    (the Proxy Agent can run several instances concurrently).
    """
    path = tmp_path / "state.json"
    error_a = make_result(False, "FAILED: HTTP 500 (1 ms)")
    error_b = make_result(False, "ERROR: no HTTP response: refused")

    first = DeviceState(path)
    second = DeviceState(path)  # loaded before `first` saves

    first.record("devA", error_a)
    first.save()
    second.record("devB", error_b)
    second.save()  # must not wipe devA

    merged = DeviceState(path)
    ok = make_result(True, "OK: HTTP 200 OK (1 ms)")
    assert merged.record("devA", ok).last_error == LastError(
        error_a.detail, error_a.checked_at
    )
    assert merged.record("devB", ok).last_error == LastError(
        error_b.detail, error_b.checked_at
    )


def test_corrupt_state_file_starts_fresh(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")

    state = DeviceState(path)  # must not raise
    record = state.record("dev1", make_result(True, "OK: HTTP 200 OK (1 ms)"))
    assert record.last_error is None
    state.save()  # must be able to overwrite the corrupt file


def test_save_without_path_is_noop():
    DeviceState().save()  # must not raise


def test_store_report_strips_sequence_and_round_trips(tmp_path):
    path = tmp_path / "state.json"
    report = {
        "device id": "dev1",
        "http response code": 200,
        "device report sequence": 7,
        "deviceReportSequence": 7,
    }

    first = DeviceState(path)
    first.store_report("dev1", report)
    first.save()

    cached = DeviceState(path).cached_report("dev1")
    assert cached["http response code"] == 200
    assert "device report sequence" not in cached
    assert "deviceReportSequence" not in cached


def test_cached_report_missing_returns_none():
    assert DeviceState().cached_report("dev1") is None


def test_forget_removes_device_even_after_merge(tmp_path):
    path = tmp_path / "state.json"
    state = DeviceState(path)
    state.record("dev1", make_result(False, "FAILED: HTTP 500 (1 ms)"))
    state.save()

    # A fresh instance forgets the device; the file copy must not resurrect
    # it through the merge-on-save logic.
    second = DeviceState(path)
    second.forget("dev1")
    second.save()

    assert DeviceState(path).record(
        "dev1", make_result(True, "OK: HTTP 200 OK (1 ms)")
    ).last_error is None
