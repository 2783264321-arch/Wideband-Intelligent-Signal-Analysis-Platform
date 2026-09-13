from benchmark_fixture import add_ground_truth, add_recording

from dataset_experiment_fixtures import G3LocalPipeline, FakeProvider, FakeRegistry

from app.pipelines.registry import PipelineRegistry


class FakeCoordinatorJobManager:
    def __init__(self, pid=4242):
        self.calls = []
        self.pid = pid

    def start(self, experiment_id, coordinator_token):
        self.calls.append((experiment_id, coordinator_token))
        return self.pid


_PAYLOAD = {
    "name": "api-exp",
    "dataset_name": "SpaceNet",
    "dataset_split": "test",
    "dataset_label_space": "spacenet_14",
    "plugin_id": "g3_local",
    "plugin_version": "1.0",
    "executor": "local_cpu",
    "parameters": {},
    "evaluation_protocol": "physical_tf_detection_ap_v2",
    "max_concurrency": 1,
}


def _prepare_app(client):
    client.app.state.pipeline_registry = PipelineRegistry([G3LocalPipeline()])
    client.app.state.executor_registry = FakeRegistry({"local_cpu": FakeProvider("local_cpu")})
    job_manager = FakeCoordinatorJobManager()
    client.app.state.dataset_experiment_job_manager = job_manager
    return job_manager


def _seed(client):
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id="rec_0", name="name_0")
        add_ground_truth(session, gt_id="gt_0", recording_id="rec_0", class_id=9,
                         class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                         f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()


def _create(client):
    _seed(client)
    _prepare_app(client)
    response = client.post("/api/dataset-experiments", json=_PAYLOAD)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_experiment_201_shape(client):
    body = _create(client)
    assert body["status"] == "pending"
    assert body["expected_items"] == 1
    assert body["queued_items"] == 1
    assert body["attempt_count"] == 0
    assert body["dataset_evaluation_id"] is None


def test_list_and_get_experiment(client):
    body = _create(client)
    listing = client.get("/api/dataset-experiments")
    assert listing.status_code == 200
    assert any(item["id"] == body["id"] for item in listing.json())
    got = client.get(f"/api/dataset-experiments/{body['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]


def test_get_experiment_not_found(client):
    _prepare_app(client)
    assert client.get("/api/dataset-experiments/missing").status_code == 404


def test_items_derived_counts(client):
    body = _create(client)
    response = client.get(f"/api/dataset-experiments/{body['id']}/items")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["status"] == "queued"
    assert items[0]["latest_analysis_run_id"] is None


def test_attempts_empty_for_item(client):
    body = _create(client)
    items = client.get(f"/api/dataset-experiments/{body['id']}/items").json()
    response = client.get(
        f"/api/dataset-experiments/{body['id']}/items/{items[0]['id']}/attempts"
    )
    assert response.status_code == 200
    assert response.json() == []


def test_attempts_ownership_mismatch_404(client):
    body = _create(client)
    items = client.get(f"/api/dataset-experiments/{body['id']}/items").json()
    response = client.get(
        f"/api/dataset-experiments/other_exp/items/{items[0]['id']}/attempts"
    )
    assert response.status_code == 404


def test_run_endpoint_202_running(client):
    job_manager = _prepare_app(client)
    _seed(client)
    created = client.post("/api/dataset-experiments", json=_PAYLOAD).json()
    response = client.post(f"/api/dataset-experiments/{created['id']}/run")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert len(job_manager.calls) == 1


def test_run_not_found_404(client):
    _prepare_app(client)
    assert client.post("/api/dataset-experiments/missing/run").status_code == 404


def test_retry_failed_invalid_transition_409(client):
    job_manager = _prepare_app(client)
    _seed(client)
    created = client.post("/api/dataset-experiments", json=_PAYLOAD).json()
    response = client.post(f"/api/dataset-experiments/{created['id']}/retry-failed")
    assert response.status_code == 409
    assert job_manager.calls == []


def test_retry_evaluation_invalid_transition_409(client):
    job_manager = _prepare_app(client)
    _seed(client)
    created = client.post("/api/dataset-experiments", json=_PAYLOAD).json()
    response = client.post(f"/api/dataset-experiments/{created['id']}/retry-evaluation")
    assert response.status_code == 409
    assert job_manager.calls == []


def test_create_rejects_unknown_field(client):
    _seed(client)
    _prepare_app(client)
    payload = dict(_PAYLOAD)
    payload["unexpected_field"] = "nope"
    response = client.post("/api/dataset-experiments", json=payload)
    assert response.status_code == 422
