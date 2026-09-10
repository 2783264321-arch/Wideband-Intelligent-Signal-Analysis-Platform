"""TASK A4 — plugin capability read model."""

_EXISTING_FIELDS = {
    "id",
    "name",
    "version",
    "label_space",
    "recommended_device",
    "cpu_supported",
    "stages",
    "inspectable_stages",
    "task_capability",
    "executors_supported",
    "recommended_executor",
}

_PLUGIN_FIELDS = {
    "plugin_api_version",
    "output_label_space",
    "input_compatibility",
    "dataset_adapters",
    "model_release_required",
    "technical_execution_capabilities",
    "recommended_execution",
}

_PRIVATE_RUNTIME_FIELDS = {
    "environment_ref",
    "environment_label",
    "runtime_ref",
    "runtime_descriptor",
    "required_remote_runtime_commit",
    "asset_manifest_path",
    "model_release_id",
}


def _pipelines_by_id(client) -> dict[str, dict]:
    response = client.get("/api/pipelines")
    assert response.status_code == 200
    return {item["id"]: item for item in response.json()}


def test_list_pipelines_includes_plugin_fields(client):
    items = _pipelines_by_id(client)
    assert {"dummy", "stft_energy_detector", "zoomspec_yolo26n_aug_combined_frn_v3"} <= set(items)

    for item in items.values():
        assert _PLUGIN_FIELDS <= set(item)
        assert item["label_space"] == item["output_label_space"]

    zoom = items["zoomspec_yolo26n_aug_combined_frn_v3"]
    assert zoom["plugin_api_version"] == 1
    assert zoom["output_label_space"] == "spacenet_14"
    assert zoom["input_compatibility"] == ["spacenet_14"]
    assert zoom["dataset_adapters"] == ["SpaceNet"]
    assert zoom["model_release_required"] is True
    assert zoom["technical_execution_capabilities"] == [
        {"executor": "remote_gpu", "device_type": "cuda", "precision": "float16"}
    ]
    assert zoom["recommended_execution"] == "remote_gpu"

    stft = items["stft_energy_detector"]
    assert stft["output_label_space"] == "signal_presence_v1"
    assert stft["label_space"] == "signal_presence_v1"
    assert stft["input_compatibility"] == []
    assert stft["technical_execution_capabilities"] == []
    assert stft["recommended_execution"] is None

    dummy = items["dummy"]
    assert dummy["output_label_space"] == "spacenet_14"
    assert dummy["label_space"] == "spacenet_14"


def test_existing_pipeline_fields_still_present(client):
    items = _pipelines_by_id(client)
    for item in items.values():
        assert _EXISTING_FIELDS <= set(item)


def test_read_model_does_not_leak_private_runtime_fields(client):
    items = _pipelines_by_id(client)
    for item in items.values():
        assert _PRIVATE_RUNTIME_FIELDS.isdisjoint(item)
        for capability in item["technical_execution_capabilities"]:
            assert set(capability) == {"executor", "device_type", "precision"}
