"""TASK A1 — PipelineDefinition plugin capability fields."""


def test_execution_capability_key():
    from app.pipelines.base import ExecutionCapability

    capability = ExecutionCapability("remote_gpu", "cuda", "float16")
    assert capability.key() == ("remote_gpu", "cuda", "float16")


def test_definition_plugin_aliases_and_output_label_space():
    from app.pipelines.base import PipelineDefinition

    definition = PipelineDefinition(
        id="p",
        name="P",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="CPU",
        cpu_supported=True,
        stages=(),
        inspectable_stages=(),
    )
    assert definition.plugin_id == "p"
    assert definition.plugin_version == "1.0"
    assert definition.resolved_output_label_space == "spacenet_14"
    assert definition.plugin_api_version == 1


def test_output_label_space_overrides():
    from app.pipelines.base import PipelineDefinition

    definition = PipelineDefinition(
        id="p",
        name="P",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="CPU",
        cpu_supported=True,
        stages=(),
        inspectable_stages=(),
        output_label_space="cpn_bandwidth_tier_v1",
    )
    assert definition.resolved_output_label_space == "cpn_bandwidth_tier_v1"
