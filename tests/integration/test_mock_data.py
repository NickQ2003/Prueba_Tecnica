from app.adapters.persistence.models import Operation, Party
from app.adapters.persistence.session import SessionLocal
from scripts.mock_data import create_mock_data
from sqlalchemy import func, select


def test_mock_data_creates_api_fixtures_and_is_idempotent():
    first = create_mock_data()
    second = create_mock_data()

    assert second == first
    assert first["policy_individual_id"]
    assert first["policy_collective_id"]
    assert first["risk_individual_id"]
    assert first["risk_collective_1_id"] != first["risk_collective_2_id"]

    with SessionLocal() as session:
        assert session.get(Operation, first["operation_create_individual_id"]).status == "SUCCEEDED"
        assert session.get(Operation, first["operation_create_collective_id"]).status == "SUCCEEDED"
        assert session.get(Operation, first["operation_needs_review_id"]).status == "NEEDS_REVIEW"
        assert (
            session.scalar(
                select(func.count())
                .select_from(Party)
                .where(Party.document_reference.like("DEMO-PERSON-%"))
            )
            == 6
        )
