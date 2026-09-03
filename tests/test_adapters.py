import os
import subprocess
from pathlib import Path

import pytest

from mquery_toolkit.core import MAX_BYTES, AdapterError
from mquery_toolkit.fabric import FabricClient, redact
from mquery_toolkit.pqtest import run_pqtest, validate_pqtest


def test_fabric_200_passes_caller_token_without_logging():
    seen = []

    def transport(url, headers, payload, timeout):
        seen.append((url, headers, payload))
        assert timeout > 0
        return {
            "status": 200,
            "headers": {"Content-Type": "application/vnd.apache.arrow.stream"},
            "body": b"arrow",
        }

    result = FabricClient(
        transport, sleeper=lambda _: None, arrow_validator=lambda _: None
    ).execute("https://example.test", "token", {"q": 1})
    assert result["body"] == b"arrow"
    assert seen[0][1]["Authorization"] == "Bearer token"
    assert redact("token") == "<redacted>"


def test_fabric_202_and_429_poll_location():
    responses = iter(
        [
            {
                "status": 202,
                "headers": {
                    "Location": "https://start/operation",
                    "Retry-After": "1",
                    "x-ms-operation-id": "op",
                },
            },
            {
                "status": 429,
                "headers": {"Retry-After": "1"},
            },
            {
                "status": 200,
                "headers": {"Content-Type": "application/vnd.apache.arrow.stream"},
                "body": b"arrow",
            },
        ]
    )
    client = FabricClient(
        lambda *_: next(responses),
        sleeper=lambda _: None,
        arrow_validator=lambda _: None,
    )
    assert client.execute("https://start", "token", {})["status"] == 200


def test_fabric_fails_closed_for_missing_token_and_error():
    client = FabricClient(lambda *_: {"status": 500}, sleeper=lambda _: None)
    with pytest.raises(AdapterError, match="token"):
        client.execute("https://start.test", "", {})
    with pytest.raises(AdapterError, match="500"):
        client.execute("https://start.test", "token", {})


def test_fabric_rejects_cross_origin_and_non_arrow():
    cross_origin = FabricClient(
        lambda *_: {
            "status": 202,
            "headers": {"Location": "https://evil.test/x", "x-ms-operation-id": "op"},
        },
        sleeper=lambda _: None,
    )
    with pytest.raises(AdapterError, match="same-origin"):
        cross_origin.execute("https://start.test", "token", {})
    non_arrow = FabricClient(
        lambda *_: {
            "status": 200,
            "headers": {"Content-Type": "application/json"},
            "body": b"{}",
        },
        sleeper=lambda _: None,
    )
    with pytest.raises(AdapterError, match="Arrow"):
        non_arrow.execute("https://start.test", "token", {})


def test_fabric_uses_operation_id_and_retries_original_post_on_429():
    seen = []
    responses = iter(
        [
            {"status": 429, "headers": {"Retry-After": "1"}},
            {"status": 202, "headers": {"x-ms-operation-id": "op-1"}},
            {
                "status": 200,
                "headers": {"Content-Type": "application/vnd.apache.arrow.stream"},
                "body": b"valid",
            },
        ]
    )

    def transport(url, _headers, payload, _timeout):
        seen.append((url, payload))
        return next(responses)

    client = FabricClient(
        transport, sleeper=lambda _: None, arrow_validator=lambda _: None
    )
    client.execute("https://api.fabric.microsoft.com/v1/query", "token", {"q": 1})
    assert seen[:2] == [
        ("https://api.fabric.microsoft.com/v1/query", {"q": 1}),
        ("https://api.fabric.microsoft.com/v1/query", {"q": 1}),
    ]
    assert seen[2] == (
        "https://api.fabric.microsoft.com/v1/operations/op-1",
        None,
    )


def test_fabric_lro_succeeded_fetches_result():
    execute_url = "https://api.fabric.microsoft.com/v1/query"
    op_url = "https://api.fabric.microsoft.com/v1/operations/op-9"
    responses = iter(
        [
            {
                "status": 202,
                "headers": {
                    "Location": op_url,
                    "x-ms-operation-id": "op-9",
                    "Retry-After": "1",
                },
            },
            {
                "status": 200,
                "headers": {"Content-Type": "application/json"},
                "body": {"status": "Running"},
            },
            {
                "status": 200,
                "headers": {"Content-Type": "application/json"},
                "body": {"status": "Succeeded"},
            },
            {
                "status": 200,
                "headers": {"Content-Type": "application/vnd.apache.arrow.stream"},
                "body": b"arrow",
            },
        ]
    )
    seen = []

    def transport(url, _headers, payload, _timeout):
        seen.append((url, payload))
        return next(responses)

    client = FabricClient(
        transport, sleeper=lambda _: None, arrow_validator=lambda _: None
    )
    payload = {"q": 1}
    result = client.execute(execute_url, "token", payload)
    assert seen == [
        (execute_url, payload),
        (op_url, None),
        (op_url, None),
        (op_url + "/result", None),
    ]
    assert result["status"] == 200


def test_fabric_result_url_keeps_query_string():
    execute_url = "https://api.fabric.microsoft.com/v1/query"
    op_url = "https://api.fabric.microsoft.com/v1/operations/op-2?api-version=1"
    responses = iter(
        [
            {
                "status": 202,
                "headers": {
                    "Location": op_url,
                    "x-ms-operation-id": "op-2",
                    "Retry-After": "1",
                },
            },
            {
                "status": 200,
                "headers": {"Content-Type": "application/json"},
                "body": {"status": "Succeeded"},
            },
            {
                "status": 200,
                "headers": {"Content-Type": "application/vnd.apache.arrow.stream"},
                "body": b"arrow",
            },
        ]
    )
    seen = []

    def transport(url, _headers, payload, _timeout):
        seen.append(url)
        return next(responses)

    client = FabricClient(
        transport, sleeper=lambda _: None, arrow_validator=lambda _: None
    )
    client.execute(execute_url, "token", {"q": 1})
    assert seen[2] == (
        "https://api.fabric.microsoft.com/v1/operations/op-2/result?api-version=1"
    )


def test_fabric_rejects_succeeded_without_operation():
    client = FabricClient(
        lambda *_: {
            "status": 200,
            "headers": {"Content-Type": "application/json"},
            "body": {"status": "Succeeded"},
        },
        sleeper=lambda _: None,
    )
    with pytest.raises(AdapterError, match="JSON status"):
        client.execute("https://api.fabric.microsoft.com/v1/query", "token", {})


def test_fabric_rejects_json_from_result_endpoint():
    op_url = "https://api.fabric.microsoft.com/v1/operations/op-1"
    responses = iter(
        [
            {
                "status": 202,
                "headers": {"Location": op_url, "x-ms-operation-id": "op-1"},
            },
            {
                "status": 200,
                "headers": {"Content-Type": "application/json"},
                "body": {"status": "Succeeded"},
            },
            {
                "status": 200,
                "headers": {"Content-Type": "application/json"},
                "body": {"status": "Succeeded"},
            },
        ]
    )
    client = FabricClient(
        lambda *_: next(responses),
        sleeper=lambda _: None,
        arrow_validator=lambda _: None,
    )
    with pytest.raises(AdapterError, match="result endpoint"):
        client.execute("https://api.fabric.microsoft.com/v1/query", "token", {})


def test_fabric_never_sends_token_to_cross_origin_location():
    seen = []

    def transport(url, headers, payload, _timeout):
        seen.append((url, headers, payload))
        return {
            "status": 202,
            "headers": {"Location": "https://attacker.invalid/op"},
        }

    with pytest.raises(AdapterError, match="same-origin"):
        FabricClient(transport, sleeper=lambda _: None).execute(
            "https://api.fabric.microsoft.com/v1/query", "secret", {}
        )
    assert [item[0] for item in seen] == ["https://api.fabric.microsoft.com/v1/query"]


def _arrow_stream(metadata=None):
    pyarrow = pytest.importorskip("pyarrow")
    sink = pyarrow.BufferOutputStream()
    schema = pyarrow.schema([("value", pyarrow.int64())], metadata=metadata)
    with pyarrow.ipc.new_stream(sink, schema) as writer:
        writer.write_batch(pyarrow.record_batch([[1]], schema=schema))
    return sink.getvalue().to_pybytes()


def test_fabric_validates_real_arrow_and_detects_error_metadata():
    def client_for(body):
        return FabricClient(
            lambda *_: {
                "status": 200,
                "headers": {"Content-Type": "application/vnd.apache.arrow.stream"},
                "body": body,
            },
            sleeper=lambda _: None,
        )

    assert (
        client_for(_arrow_stream()).execute(
            "https://api.fabric.microsoft.com/v1/query", "token", {}
        )["status"]
        == 200
    )
    error_stream = _arrow_stream(
        {b"IsError": b"true", b"FaultCode": b"E1", b"FaultString": b"bad query"}
    )
    with pytest.raises(AdapterError, match=r"E1.*bad query"):
        client_for(error_stream).execute(
            "https://api.fabric.microsoft.com/v1/query", "token", {}
        )


def test_fabric_rejects_oversized_arrow_body():
    client = FabricClient(
        lambda *_: {
            "status": 200,
            "headers": {"Content-Type": "application/vnd.apache.arrow.stream"},
            "body": b"x" * (MAX_BYTES + 1),
        },
        sleeper=lambda _: None,
    )
    with pytest.raises(AdapterError, match="10 MiB"):
        client.execute("https://api.fabric.microsoft.com/v1/query", "token", {})


@pytest.mark.skipif(
    os.name == "nt",
    reason="asserts the non-Windows guard; on Windows it does not apply",
)
def test_pqtest_rejects_non_windows_without_running_binary(tmp_path: Path):
    with pytest.raises(AdapterError, match="Windows"):
        validate_pqtest(tmp_path / "PQTest.exe")


def test_pqtest_rejects_invalid_windows_prerequisites(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("mquery_toolkit.pqtest.platform.system", lambda: "Windows")
    executable = tmp_path / "PQTest.exe"
    executable.write_text("not a binary")
    completed = subprocess.CompletedProcess([], 0, stdout="wrong version", stderr="")
    with pytest.raises(AdapterError, match="2.155.2"):
        validate_pqtest(executable, runner=lambda *_args, **_kwargs: completed)


def test_pqtest_requires_exact_version_and_zero_exit(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("mquery_toolkit.pqtest.platform.system", lambda: "Windows")
    executable = tmp_path / "PQTest.exe"
    executable.write_text("test seam")
    wrong = subprocess.CompletedProcess([], 0, stdout="2.155.20", stderr="")
    with pytest.raises(AdapterError, match="2.155.2"):
        validate_pqtest(executable, runner=lambda *_args, **_kwargs: wrong)
    failed = subprocess.CompletedProcess([], 7, stdout="2.155.2", stderr="")
    with pytest.raises(AdapterError, match="exited 7"):
        validate_pqtest(executable, runner=lambda *_args, **_kwargs: failed)


def test_pqtest_wrapper_validates_then_runs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("mquery_toolkit.pqtest.platform.system", lambda: "Windows")
    executable = tmp_path / "PQTest.exe"
    executable.write_text("test seam")
    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        output = "2.155.2" if command[-1] == "version" else '{"Status":"Success"}'
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    assert "Success" in run_pqtest(executable, ["run-test"], runner=runner)
    assert commands == [
        [str(executable), "version"],
        [str(executable), "run-test"],
    ]
