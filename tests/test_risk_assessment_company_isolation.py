from copy import deepcopy

import pytest


class DataResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name, query_log):
        self.client = client
        self.table_name = table_name
        self.query_log = query_log

    def select(self, columns):
        self.query_log["operation"] = "select"
        self.query_log["columns"] = columns
        return self

    def insert(self, payload):
        self.query_log["operation"] = "insert"
        self.query_log["payload"] = deepcopy(payload)
        return self

    def update(self, payload):
        self.query_log["operation"] = "update"
        self.query_log["payload"] = deepcopy(payload)
        return self

    def delete(self):
        self.query_log["operation"] = "delete"
        return self

    def eq(self, column, value):
        self.query_log["filters"].append(("eq", column, value))
        return self

    def _matches(self, record):
        return all(
            operation == "eq" and record.get(column) == value
            for operation, column, value in self.query_log["filters"]
        )

    def execute(self):
        operation = self.query_log["operation"]
        records = self.client.records[self.table_name]

        if operation == "select":
            return DataResponse([
                deepcopy(record) for record in records if self._matches(record)
            ])

        if operation == "insert":
            payload = deepcopy(self.query_log["payload"])
            records.append(payload)
            return DataResponse([deepcopy(payload)])

        if operation == "update":
            updated = []
            for record in records:
                if self._matches(record):
                    record.update(deepcopy(self.query_log["payload"]))
                    updated.append(deepcopy(record))
            return DataResponse(updated)

        if operation == "delete":
            deleted = [deepcopy(record) for record in records if self._matches(record)]
            self.client.records[self.table_name] = [
                record for record in records if not self._matches(record)
            ]
            return DataResponse(deleted)

        raise AssertionError("A database operation must be selected before execute().")


class FakeSupabase:
    def __init__(self, records=None):
        self.records = deepcopy(records or {
            "assets": [],
            "risk_assessments": [],
        })
        self.queries = []

    def table(self, table_name):
        query_log = {
            "table": table_name,
            "operation": None,
            "columns": None,
            "payload": None,
            "filters": [],
        }
        self.queries.append(query_log)
        return FakeQuery(self, table_name, query_log)


@pytest.fixture()
def supabase_db(monkeypatch):
    from riskGenie.models import supabase_db as module

    def reject_real_supabase():
        raise AssertionError("A real Supabase client must not be used in tests.")

    monkeypatch.setattr(module, "get_supabase_client", reject_real_supabase)
    return module


def install_fake(supabase_db, monkeypatch, records=None):
    fake = FakeSupabase(records)
    monkeypatch.setattr(supabase_db, "get_supabase_client", lambda: fake)
    return fake


def query_for(fake, table_name, operation):
    return next(
        query
        for query in fake.queries
        if query["table"] == table_name and query["operation"] == operation
    )


def test_save_risk_assessment_requires_company_id(supabase_db):
    with pytest.raises(TypeError):
        supabase_db.save_risk_assessment({"asset_id": 1})


@pytest.mark.parametrize(
    "company_id",
    [None, True, False, 0, -1, "7", 7.0, [], {}],
)
def test_save_risk_assessment_rejects_invalid_company_id(
    supabase_db,
    company_id,
):
    with pytest.raises(supabase_db.InvalidCompanyContextError):
        supabase_db.save_risk_assessment(
            {"asset_id": 1},
            company_id=company_id,
        )


def test_save_risk_assessment_requires_dict(supabase_db):
    with pytest.raises(supabase_db.InvalidRiskAssessmentError):
        supabase_db.save_risk_assessment([], company_id=7)


def test_save_risk_assessment_requires_asset_id(supabase_db, monkeypatch):
    fake = install_fake(supabase_db, monkeypatch)

    with pytest.raises(
        supabase_db.InvalidRiskAssessmentError,
        match="asset_id is required",
    ):
        supabase_db.save_risk_assessment(
            {"risk_score": 8.5},
            company_id=7,
        )

    assert not fake.queries


def test_missing_asset_id_does_not_get_supabase_client(
    supabase_db,
    monkeypatch,
):
    client_calls = []

    def record_client_call():
        client_calls.append(True)
        return FakeSupabase()

    monkeypatch.setattr(
        supabase_db,
        "get_supabase_client",
        record_client_call,
    )

    with pytest.raises(supabase_db.InvalidRiskAssessmentError):
        supabase_db.save_risk_assessment(
            {"risk_score": 8.5},
            company_id=7,
        )

    assert client_calls == []


def test_save_uses_server_company_and_does_not_mutate_caller(
    supabase_db,
    monkeypatch,
):
    fake = install_fake(
        supabase_db,
        monkeypatch,
        {
            "assets": [
                {"id": 7001, "company_id": 7, "is_deleted": False}
            ],
            "risk_assessments": [],
        },
    )
    assessment_data = {
        "asset_id": 7001,
        "company_id": 99,
        "risk_score": 8.5,
    }

    result = supabase_db.save_risk_assessment(
        assessment_data,
        company_id=7,
    )

    assert assessment_data["company_id"] == 99
    assert result[0]["company_id"] == 7
    assert query_for(fake, "assets", "select")["filters"] == [
        ("eq", "id", 7001),
        ("eq", "company_id", 7),
        ("eq", "is_deleted", False),
    ]
    assert query_for(fake, "risk_assessments", "insert")["payload"] == {
        "asset_id": 7001,
        "company_id": 7,
        "risk_score": 8.5,
    }


def test_save_removes_client_id_without_mutating_caller(
    supabase_db,
    monkeypatch,
):
    fake = install_fake(
        supabase_db,
        monkeypatch,
        {
            "assets": [
                {"id": 7001, "company_id": 7, "is_deleted": False}
            ],
            "risk_assessments": [],
        },
    )
    assessment_data = {
        "id": 999,
        "asset_id": 7001,
        "risk_score": 8.5,
    }

    supabase_db.save_risk_assessment(assessment_data, company_id=7)

    insert_payload = query_for(fake, "risk_assessments", "insert")["payload"]
    assert "id" not in insert_payload
    assert assessment_data["id"] == 999


def test_save_rejects_soft_deleted_asset_without_insert(
    supabase_db,
    monkeypatch,
):
    fake = install_fake(
        supabase_db,
        monkeypatch,
        {
            "assets": [
                {"id": 7001, "company_id": 7, "is_deleted": True}
            ],
            "risk_assessments": [],
        },
    )

    with pytest.raises(
        supabase_db.RiskAssessmentAssetNotFoundError,
        match="Asset not found",
    ):
        supabase_db.save_risk_assessment(
            {"asset_id": 7001, "risk_score": 8.5},
            company_id=7,
        )

    assert query_for(fake, "assets", "select")["filters"] == [
        ("eq", "id", 7001),
        ("eq", "company_id", 7),
        ("eq", "is_deleted", False),
    ]
    assert fake.records["risk_assessments"] == []
    assert not any(
        query["operation"] == "insert" for query in fake.queries
    )


@pytest.mark.parametrize(
    "assets",
    [
        [],
        [{"id": 9901, "company_id": 99}],
    ],
)
def test_missing_or_cross_company_asset_does_not_insert(
    supabase_db,
    monkeypatch,
    assets,
):
    fake = install_fake(
        supabase_db,
        monkeypatch,
        {"assets": assets, "risk_assessments": []},
    )

    with pytest.raises(supabase_db.RiskAssessmentAssetNotFoundError) as error:
        supabase_db.save_risk_assessment(
            {"asset_id": 9901},
            company_id=7,
        )

    assert str(error.value) == "Asset not found."
    assert not any(
        query["operation"] == "insert" for query in fake.queries
    )


@pytest.mark.parametrize("asset_id", [None, True, False, 0, -1, "7001", 1.5])
def test_save_rejects_invalid_asset_id(
    supabase_db,
    monkeypatch,
    asset_id,
):
    client_calls = []

    def record_client_call():
        client_calls.append(True)
        return FakeSupabase()

    monkeypatch.setattr(
        supabase_db,
        "get_supabase_client",
        record_client_call,
    )

    with pytest.raises(supabase_db.InvalidRiskAssessmentError):
        supabase_db.save_risk_assessment(
            {"asset_id": asset_id},
            company_id=7,
        )

    assert client_calls == []


def test_get_all_risk_assessments_requires_company_id(supabase_db):
    with pytest.raises(TypeError):
        supabase_db.get_all_risk_assessments()


@pytest.mark.parametrize("company_id", [None, True, False, 0, -1, "7", 7.0])
def test_get_all_risk_assessments_rejects_invalid_company_id(
    supabase_db,
    company_id,
):
    with pytest.raises(supabase_db.InvalidCompanyContextError):
        supabase_db.get_all_risk_assessments(company_id)


def test_get_all_risk_assessments_filters_company(
    supabase_db,
    monkeypatch,
):
    fake = install_fake(
        supabase_db,
        monkeypatch,
        {
            "assets": [],
            "risk_assessments": [
                {"id": 1, "company_id": 7},
                {"id": 2, "company_id": 99},
            ],
        },
    )

    result = supabase_db.get_all_risk_assessments(company_id=7)

    assert result == [{"id": 1, "company_id": 7}]
    assert query_for(fake, "risk_assessments", "select")["filters"] == [
        ("eq", "company_id", 7)
    ]


def test_update_risk_assessment_uses_id_and_company_filters(
    supabase_db,
    monkeypatch,
):
    fake = install_fake(
        supabase_db,
        monkeypatch,
        {
            "assets": [],
            "risk_assessments": [
                {"id": 1, "company_id": 7, "status": "open"},
                {"id": 1, "company_id": 99, "status": "open"},
            ],
        },
    )

    result = supabase_db.update_risk_assessment(
        1,
        {"status": "closed", "company_id": 99},
        company_id=7,
    )

    query = query_for(fake, "risk_assessments", "update")
    assert query["filters"] == [
        ("eq", "id", 1),
        ("eq", "company_id", 7),
    ]
    assert query["payload"]["company_id"] == 7
    assert result == [{"id": 1, "company_id": 7, "status": "closed"}]
    assert fake.records["risk_assessments"][1]["status"] == "open"


def test_update_removes_client_id_without_mutating_caller(
    supabase_db,
    monkeypatch,
):
    fake = install_fake(
        supabase_db,
        monkeypatch,
        {
            "assets": [],
            "risk_assessments": [
                {"id": 1, "company_id": 7, "status": "open"},
            ],
        },
    )
    assessment_data = {"id": 999, "status": "closed"}

    result = supabase_db.update_risk_assessment(
        1,
        assessment_data,
        company_id=7,
    )

    update_payload = query_for(fake, "risk_assessments", "update")["payload"]
    assert "id" not in update_payload
    assert assessment_data["id"] == 999
    assert result == [{"id": 1, "company_id": 7, "status": "closed"}]


def test_delete_risk_assessment_uses_id_and_company_filters(
    supabase_db,
    monkeypatch,
):
    fake = install_fake(
        supabase_db,
        monkeypatch,
        {
            "assets": [],
            "risk_assessments": [
                {"id": 1, "company_id": 7},
                {"id": 1, "company_id": 99},
            ],
        },
    )

    result = supabase_db.delete_risk_assessment(1, company_id=7)

    query = query_for(fake, "risk_assessments", "delete")
    assert query["filters"] == [
        ("eq", "id", 1),
        ("eq", "company_id", 7),
    ]
    assert result == [{"id": 1, "company_id": 7}]
    assert fake.records["risk_assessments"] == [{"id": 1, "company_id": 99}]
