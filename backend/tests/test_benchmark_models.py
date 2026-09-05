from sqlalchemy import inspect, select

from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database


def test_benchmark_tables_are_registered_and_created(settings):
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    names = set(inspect(database.engine).get_table_names())
    assert "dataset_evaluations" in names
    assert "dataset_evaluation_items" in names


def test_additive_migration_creates_benchmark_tables_for_existing_database(settings):
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.tables["recordings"].create(database.engine, checkfirst=True)
    Base.metadata.tables["analysis_runs"].create(database.engine, checkfirst=True)
    run_additive_migrations(database.engine)
    names = set(inspect(database.engine).get_table_names())
    assert "dataset_evaluations" in names
    assert "dataset_evaluation_items" in names


def test_evaluation_and_items_persist_with_deterministic_order(settings):
    from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel

    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)

    with database.session_factory() as session:
        evaluation = DatasetEvaluationModel(
            id="eval_test",
            name="tiny",
            dataset_name="SpaceNet",
            dataset_split="test",
            label_space="spacenet_14",
            pipeline_id="pipeline_x",
            pipeline_version="1.0",
            status="pending",
            expected_recordings=2,
            evaluated_recordings=1,
            missing_recordings=1,
            coverage=0.5,
            comparable=False,
            recording_manifest_hash="a" * 64,
            evaluation_protocol="physical_tf_detection_ap_v1",
            protocol_config_json={"iou_thresholds": [0.5]},
        )
        items = [
            DatasetEvaluationItemModel(
                id="evalitem_1",
                evaluation_id=evaluation.id,
                manifest_order=0,
                recording_id="rec_1",
                analysis_run_id="run_1",
                status="included",
                gt_count=2,
                prediction_count=3,
            ),
            DatasetEvaluationItemModel(
                id="evalitem_2",
                evaluation_id=evaluation.id,
                manifest_order=1,
                recording_id="rec_2",
                analysis_run_id=None,
                status="missing_run",
                gt_count=1,
                prediction_count=0,
            ),
        ]
        session.add(evaluation)
        session.add_all(items)
        session.commit()

    with database.session_factory() as fresh:
        stored = fresh.get(DatasetEvaluationModel, "eval_test")
        assert stored is not None
        assert stored.items[0].manifest_order == 0
        assert stored.items[1].manifest_order == 1
        assert stored.items[1].analysis_run_id is None
        assert [item.id for item in stored.items] == ["evalitem_1", "evalitem_2"]
        rows = fresh.scalars(
            select(DatasetEvaluationItemModel).where(
                DatasetEvaluationItemModel.evaluation_id == "eval_test"
            ).order_by(DatasetEvaluationItemModel.manifest_order)
        ).all()
        assert len(rows) == 2