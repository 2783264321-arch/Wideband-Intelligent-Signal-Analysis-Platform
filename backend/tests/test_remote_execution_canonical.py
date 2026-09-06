import pytest

from app.remote_execution.canonical import (
    canonical_request_bytes,
    canonical_request_payload,
    compute_request_sha256,
)
from app.remote_execution.schema import (
    RemoteExecutionBatchV1,
    RemoteExecutionItemV1,
    RemoteRecordingRefV1,
)


def _batch(request_sha256="a" * 64, **overrides):
    values = dict(
        schema_version=1,
        batch_id="batch_x",
        required_remote_runtime_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
        pipeline={"id": "zoomspec_yolo26n_aug_combined_frn_v3", "version": "1.0.0"},
        asset_manifest_sha256="c" * 64,
        items=[
            RemoteExecutionItemV1(
                item_key="000000",
                request_id="req_000000",
                local_run_id="run_a",
                orchestrator_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
                recording=RemoteRecordingRefV1(
                    dataset_name="SpaceNet",
                    dataset_split="test",
                    dataset_key="0",
                    label_space="spacenet_14",
                    expected_recording_fingerprint="b" * 64,
                    expected_source_data_sha256="c" * 64,
                ),
                parameters={},
            )
        ],
        request_sha256=request_sha256,
    )
    values.update(overrides)
    return RemoteExecutionBatchV1(**values)


def _with_parameters(params):
    return _batch(
        items=[
            RemoteExecutionItemV1(
                item_key="000000",
                request_id="req_000000",
                local_run_id="run_a",
                orchestrator_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
                recording=RemoteRecordingRefV1(
                    dataset_name="SpaceNet",
                    dataset_split="test",
                    dataset_key="0",
                    label_space="spacenet_14",
                    expected_recording_fingerprint="b" * 64,
                    expected_source_data_sha256="c" * 64,
                ),
                parameters=params,
            )
        ]
    )


def test_request_sha256_excludes_itself():
    a = compute_request_sha256(_batch(request_sha256="a" * 64))
    b = compute_request_sha256(_batch(request_sha256="b" * 64))
    assert a == b


def test_canonical_payload_has_no_request_sha256_key():
    payload = canonical_request_payload(_batch())
    assert "request_sha256" not in payload


def test_canonical_payload_includes_semantic_fields():
    payload = canonical_request_payload(_batch())
    assert payload["schema_version"] == 1
    assert payload["batch_id"] == "batch_x"
    assert payload["required_remote_runtime_commit"] == "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"
    assert payload["pipeline"] == {"id": "zoomspec_yolo26n_aug_combined_frn_v3", "version": "1.0.0"}
    assert payload["asset_manifest_sha256"] == "c" * 64
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["item_key"] == "000000"
    assert item["request_id"] == "req_000000"
    assert item["local_run_id"] == "run_a"
    assert item["orchestrator_commit"] == "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"
    assert item["recording"]["dataset_name"] == "SpaceNet"
    assert "parameters" in item


def test_request_sha256_is_deterministic_and_64_hex():
    first = compute_request_sha256(_batch())
    second = compute_request_sha256(_batch())
    assert first == second
    assert len(first) == 64
    assert all(c in "0123456789abcdef" for c in first)


def test_batch_id_change_changes_hash():
    base = compute_request_sha256(_batch())
    changed = _batch(batch_id="batch_y")
    assert compute_request_sha256(changed) != base


def test_parameter_change_changes_hash():
    base = compute_request_sha256(_batch())
    changed = _with_parameters({"alpha": 1})
    assert compute_request_sha256(changed) != base


def test_recording_identity_change_changes_hash():
    base = compute_request_sha256(_batch())
    changed = _batch(
        items=[
            RemoteExecutionItemV1(
                item_key="000000",
                request_id="req_000000",
                local_run_id="run_a",
                orchestrator_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
                recording=RemoteRecordingRefV1(
                    dataset_name="SpaceNet",
                    dataset_split="test",
                    dataset_key="1",
                    label_space="spacenet_14",
                    expected_recording_fingerprint="b" * 64,
                    expected_source_data_sha256="c" * 64,
                ),
                parameters={},
            )
        ]
    )
    assert compute_request_sha256(changed) != base


def test_dict_field_order_independent():
    first = compute_request_sha256(_with_parameters({"alpha": 1, "beta": 2}))
    second = compute_request_sha256(_with_parameters({"beta": 2, "alpha": 1}))
    assert first == second


def test_nested_dict_field_order_independent():
    a = _with_parameters({"nested": {"x": 1, "y": 2}})
    b = _with_parameters({"nested": {"y": 2, "x": 1}})
    assert compute_request_sha256(a) == compute_request_sha256(b)


def test_list_order_is_semantic():
    a = compute_request_sha256(_with_parameters({"xs": [1, 2]}))
    b = compute_request_sha256(_with_parameters({"xs": [2, 1]}))
    assert a != b


def test_number_vs_string_distinct():
    a = compute_request_sha256(_with_parameters({"x": 1.0}))
    b = compute_request_sha256(_with_parameters({"x": "1"}))
    assert a != b


def test_negative_zero_canonicalizes_like_zero():
    a = compute_request_sha256(_with_parameters({"x": -0.0}))
    b = compute_request_sha256(_with_parameters({"x": 0.0}))
    assert a == b


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_rejected(bad):
    batch = _with_parameters({"x": bad})
    with pytest.raises(ValueError):
        canonical_request_bytes(canonical_request_payload(batch))


def test_utf8_escaping_deterministic():
    a = _with_parameters({"name": "带宽\u03bb"})
    b = _with_parameters({"name": "带宽\u03bb"})
    assert compute_request_sha256(a) == compute_request_sha256(b)


def test_canonical_bytes_are_utf8_json_without_insignificant_whitespace():
    payload = canonical_request_payload(_with_parameters({"x": 1.0}))
    raw = canonical_request_bytes(payload)
    text = raw.decode("utf-8")
    assert not text.startswith(" ")
    assert " : " not in text and ": " not in text


def test_canonical_rejects_non_string_dict_key_with_intentional_error():
    with pytest.raises(ValueError, match="non-string dict key"):
        canonical_request_bytes({"x": {"a": 1, 2: "bad"}})