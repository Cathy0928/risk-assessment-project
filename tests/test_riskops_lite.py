"""
RiskOps Lite tests.

Self-contained (mirrors the FakeSupabase pattern already used in
test_risk_phase1.py / test_risk_assessment_company_isolation.py) so this
file does not depend on any real Supabase project, real Gemini API, or a
running database. Every test uses an in-memory fake client.
"""

import importlib
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ================================================================
# App / client fixtures
# ================================================================

@pytest.fixture()
def app_module(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("FLASK_ENV", "development")

    module = importlib.import_module("riskGenie.app")
    return importlib.reload(module)


@pytest.fixture()
def app(app_module):
    return app_module.create_app({"TESTING": True, "SECRET_KEY": "test-secret"})


@pytest.fixture()
def client(app):
    return app.test_client()


def login_as(client, company_id=7, user_id="user-id"):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = user_id
        sess["username"] = "Peggy"
        sess["email"] = "peggy@example.com"
        sess["role_name"] = "user"
        sess["company_id"] = company_id


# ================================================================
# Fake Supabase client (select / insert / update)
# ================================================================

class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.filters = []
        self.limit_value = None
        self.operation = "select"
        self.insert_payload = None
        self.update_payload = None
        self.order_field = None
        self.order_desc = False

    def select(self, fields):
        self.operation = "select"
        self.selected_fields = fields
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.insert_payload = deepcopy(payload)
        return self

    def update(self, payload):
        self.operation = "update"
        self.update_payload = deepcopy(payload)
        return self

    def eq(self, field, value):
        self.filters.append(("eq", field, value))
        return self

    def in_(self, field, values):
        self.filters.append(("in", field, list(values)))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def order(self, field, desc=False, **_kwargs):
        self.order_field = field
        self.order_desc = desc
        return self

    def _matches(self, record):
        for operation, field, value in self.filters:
            if operation == "eq":
                if record.get(field) != value:
                    return False
            else:
                if record.get(field) not in value:
                    return False
        return True

    def execute(self):
        self.client.queries.append({
            "table": self.table_name,
            "operation": self.operation,
            "filters": list(self.filters),
        })

        if self.operation == "insert":
            inserted = deepcopy(self.insert_payload)
            # 真實 Supabase 會在 insert 時自動產生 bigint id；
            # 這個 fake 也要模擬同樣的行為，否則依賴
            # response.data[0]["id"] 的 contract 測試會失真。
            inserted.setdefault(
                "id",
                len(self.client.records.get(self.table_name, [])) + 1,
            )
            self.client.records.setdefault(self.table_name, []).append(inserted)
            return SimpleNamespace(data=[inserted])

        if self.operation == "update":
            updated = []
            for record in self.client.records.get(self.table_name, []):
                if self._matches(record):
                    record.update(deepcopy(self.update_payload))
                    updated.append(record.copy())
            return SimpleNamespace(data=updated)

        records = [
            record.copy()
            for record in self.client.records.get(self.table_name, [])
            if self._matches(record)
        ]

        if self.order_field is not None:
            records.sort(
                key=lambda record: record.get(self.order_field),
                reverse=self.order_desc,
            )

        if self.limit_value is not None:
            records = records[:self.limit_value]

        return SimpleNamespace(data=records)


class FakeSupabase:
    def __init__(self, assets=None, assessments=None, audit_logs=None):
        self.records = {
            "assets": list(assets or []),
            "risk_assessments": list(assessments or []),
            "audit_logs": list(audit_logs or []),
        }
        self.queries = []

    def table(self, table_name):
        return FakeQuery(self, table_name)


class RaisingFakeSupabase(FakeSupabase):
    """Simulates a Postgrest 'column does not exist' error on write.

    Used to prove the RiskOps endpoints fail closed with a clean JSON
    error instead of leaking a raw exception when a required DB column
    is missing (see the schema-drift risk flagged in the prior review).
    """

    def __init__(self, *args, fail_table, fail_operations=("update", "insert"), **kwargs):
        super().__init__(*args, **kwargs)
        self._fail_table = fail_table
        self._fail_operations = set(fail_operations)

    def table(self, table_name):
        query = FakeQuery(self, table_name)

        if table_name == self._fail_table:
            original_execute = query.execute

            def guarded_execute():
                if query.operation in self._fail_operations:
                    raise Exception(
                        'column "status" of relation "risk_assessments" '
                        "does not exist"
                    )
                return original_execute()

            query.execute = guarded_execute

        return query


class ZeroRowUpdateFakeSupabase(FakeSupabase):
    """Simulates an UPDATE that "succeeds" (no exception) but affects
    zero rows — e.g. an RLS write policy that silently filters out the
    row even though the matching SELECT could see it. `.execute()`
    returns `data=[]` instead of the updated row.

    This is the shape of bug that P0 closes: previously the route code
    treated `update_response.data or []` as success even when it was
    an empty list.
    """

    def __init__(self, *args, zero_row_table, **kwargs):
        super().__init__(*args, **kwargs)
        self._zero_row_table = zero_row_table

    def table(self, table_name):
        query = FakeQuery(self, table_name)

        if table_name == self._zero_row_table:
            original_execute = query.execute

            def guarded_execute():
                if query.operation == "update":
                    self.queries.append({
                        "table": table_name,
                        "operation": "update",
                        "filters": list(query.filters),
                    })
                    return SimpleNamespace(data=[])
                return original_execute()

            query.execute = guarded_execute

        return query


class WrongRowUpdateFakeSupabase(FakeSupabase):
    """Simulates an UPDATE whose response row doesn't match what we
    asked for — e.g. a buggy RPC/trigger returning a stale or
    unrelated row instead of the one addressed by the WHERE clause.

    The route must never treat this as a persisted success just
    because `data` is non-empty; it has to check the row's own id.
    """

    def __init__(self, *args, wrong_row_table, wrong_row, **kwargs):
        super().__init__(*args, **kwargs)
        self._wrong_row_table = wrong_row_table
        self._wrong_row = wrong_row

    def table(self, table_name):
        query = FakeQuery(self, table_name)

        if table_name == self._wrong_row_table:
            original_execute = query.execute

            def guarded_execute():
                if query.operation == "update":
                    self.queries.append({
                        "table": table_name,
                        "operation": "update",
                        "filters": list(query.filters),
                    })
                    return SimpleNamespace(
                        data=[deepcopy(self._wrong_row)]
                    )
                return original_execute()

            query.execute = guarded_execute

        return query


def install_fake_supabase(monkeypatch, fake):
    from riskGenie.models import supabase_db
    from riskGenie.services import risk_routes

    monkeypatch.setattr(risk_routes, "get_supabase_client", lambda: fake)
    monkeypatch.setattr(supabase_db, "get_supabase_client", lambda: fake)
    monkeypatch.setattr(risk_routes, "get_supabase_admin_client", lambda: fake)
    monkeypatch.setattr(supabase_db, "get_supabase_admin_client", lambda: fake)
    return fake


def asset_record(asset_id=701, company_id=7, **overrides):
    record = {
        "id": asset_id,
        "company_id": company_id,
        "asset_id_code": f"ASSET-{asset_id}",
        "asset_name": "Web Server",
        "asset_type": "伺服器",
        "description": "Production web server",
        "confidentiality": 5,
        "integrity": 4,
        "availability": 5,
        "legality": 3,
        "asset_value": 5,
        "risk_owner": "alice",
        "is_deleted": False,
    }
    record.update(overrides)
    return record


def assessment_record(assessment_id, asset_id=701, company_id=7, **overrides):
    record = {
        "id": assessment_id,
        "asset_id": asset_id,
        "company_id": company_id,
        "status": "待處理",
        "ai_suggestion": None,
        "treatment_note": None,
        "treatment_due_date": None,
        "evidence_url": None,
        "threat_description": "公開服務存在高風險弱點",
        "cvss_score": 9.8,
        "likelihood_score": 5,
        "impact_score": 9.8,
        "risk_score": 49,
        "risk_level": "極高風險",
        "created_at": "2026-01-01T00:00:00",
    }
    record.update(overrides)
    return record


def valid_ai_payload(**overrides):
    payload = {
        "asset_id": 701,
        "asset_name": "Web Server",
        "confidentiality": 5,
        "integrity": 4,
        "availability": 5,
        "legality": 3,
        "cvss_score": 9.8,
        "likelihood_score": 5,
        "impact_score": 9.8,
        "risk_score": 49,
        "risk_level": "極高風險",
    }
    payload.update(overrides)
    return payload


def valid_assessment_payload(**overrides):
    payload = {
        "asset_id": 701,
        "threat_description": "公開服務存在高風險弱點",
        "impact_score": 9.8,
        "likelihood_score": 5,
        "cvss_score": 9.8,
        "risk_score": 49,
        "risk_level": "極高風險",
    }
    payload.update(overrides)
    return payload


def enable_ai(monkeypatch, advice="請優先修補公開服務。"):
    from riskGenie.services import risk_routes

    monkeypatch.setattr(risk_routes, "is_gemini_configured", lambda: True)
    monkeypatch.setattr(
        risk_routes,
        "generate_advice",
        lambda **_kwargs: advice,
    )


# ================================================================
# 1. 同一資產有多筆評鑑：AI 建議寫入正確紀錄
# ================================================================

def test_ai_advice_with_explicit_assessment_id_updates_only_that_record(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[asset_record()],
        assessments=[
            assessment_record(1, created_at="2026-01-01T00:00:00"),
            assessment_record(2, created_at="2026-02-01T00:00:00"),
        ],
    )
    install_fake_supabase(monkeypatch, fake)
    enable_ai(monkeypatch)
    login_as(client)

    response = client.post(
        "/api/ai-advice",
        json=valid_ai_payload(assessment_id=1),
    )

    assert response.status_code == 200
    assert response.get_json()["assessment_id"] == 1

    by_id = {row["id"]: row for row in fake.records["risk_assessments"]}
    assert by_id[1]["ai_suggestion"] == "請優先修補公開服務。"
    assert by_id[2]["ai_suggestion"] is None


def test_ai_advice_without_assessment_id_returns_preview_without_writing(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[asset_record()],
        assessments=[
            assessment_record(1, created_at="2026-01-01T00:00:00"),
            assessment_record(2, created_at="2026-02-01T00:00:00"),
        ],
    )
    install_fake_supabase(monkeypatch, fake)
    enable_ai(monkeypatch)
    login_as(client)

    response = client.post("/api/ai-advice", json=valid_ai_payload())

    assert response.status_code == 200
    assert response.get_json()["assessment_id"] is None

    by_id = {row["id"]: row for row in fake.records["risk_assessments"]}
    assert by_id[2]["ai_suggestion"] is None
    assert by_id[1]["ai_suggestion"] is None


def test_ai_advice_rejects_assessment_id_from_other_company(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[asset_record()],
        assessments=[
            assessment_record(1, company_id=99, asset_id=701),
        ],
    )
    install_fake_supabase(monkeypatch, fake)
    enable_ai(monkeypatch)
    login_as(client, company_id=7)

    response = client.post(
        "/api/ai-advice",
        json=valid_ai_payload(assessment_id=1),
    )

    assert response.status_code == 404
    assert response.get_json()["code"] == "ASSESSMENT_NOT_FOUND"
    assert fake.records["risk_assessments"][0]["ai_suggestion"] is None


def test_ai_advice_rejects_assessment_id_for_different_asset(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[asset_record(asset_id=701), asset_record(asset_id=702)],
        assessments=[
            assessment_record(1, asset_id=702, company_id=7),
        ],
    )
    install_fake_supabase(monkeypatch, fake)
    enable_ai(monkeypatch)
    login_as(client)

    response = client.post(
        "/api/ai-advice",
        json=valid_ai_payload(asset_id=701, assessment_id=1),
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "ASSESSMENT_ASSET_MISMATCH"
    assert fake.records["risk_assessments"][0]["ai_suggestion"] is None


def test_ai_advice_does_not_touch_cia_or_score_fields(client, monkeypatch):
    fake = FakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
    )
    install_fake_supabase(monkeypatch, fake)
    enable_ai(monkeypatch)
    login_as(client)

    client.post(
        "/api/ai-advice",
        json=valid_ai_payload(assessment_id=1),
    )

    stored = fake.records["risk_assessments"][0]
    assert stored["cvss_score"] == 9.8
    assert stored["risk_score"] == 49
    assert stored["risk_level"] == "極高風險"
    assert stored["status"] == "待處理"  # AI 建議失敗/成功都不動 status


# ================================================================
# 2. 不同公司不能讀取或修改彼此的處置紀錄
# ================================================================

def test_riskops_get_requires_login(client):
    response = client.get("/api/risk-assessments/1/riskops")
    assert response.status_code == 401
    assert response.get_json()["code"] == "UNAUTHORIZED"


def test_riskops_post_requires_login(client):
    response = client.post("/api/risk-assessments/1/riskops", json={})
    assert response.status_code == 401
    assert response.get_json()["code"] == "UNAUTHORIZED"


def test_get_riskops_rejects_cross_company_assessment(client, monkeypatch):
    fake = FakeSupabase(
        assets=[asset_record(company_id=99)],
        assessments=[assessment_record(1, company_id=99)],
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.get("/api/risk-assessments/1/riskops")

    assert response.status_code == 404
    assert response.get_json()["code"] == "ASSESSMENT_NOT_FOUND"


def test_save_riskops_rejects_cross_company_assessment(client, monkeypatch):
    fake = FakeSupabase(
        assets=[asset_record(company_id=99)],
        assessments=[assessment_record(1, company_id=99)],
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.post(
        "/api/risk-assessments/1/riskops",
        json={
            "treatment_note": "駭進去改的",
            "status": "已完成",
        },
    )

    assert response.status_code == 404
    assert response.get_json()["code"] == "ASSESSMENT_NOT_FOUND"

    # 另一家公司的紀錄完全沒被動過。
    stored = fake.records["risk_assessments"][0]
    assert stored["treatment_note"] is None
    assert stored["status"] == "待處理"


# ================================================================
# 3. 改善內容可以儲存並重新讀取
# ================================================================

def test_save_and_reload_riskops_round_trip(client, monkeypatch):
    fake = FakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client)

    save_response = client.post(
        "/api/risk-assessments/1/riskops",
        json={
            "treatment_note": "已封鎖對外連線並套用修補",
            "treatment_due_date": "2026-10-01",
            "evidence_url": "https://example.com/evidence.png",
            "status": "處理中",
        },
    )

    assert save_response.status_code == 200
    assert save_response.get_json()["status"] == "處理中"

    get_response = client.get("/api/risk-assessments/1/riskops")
    assert get_response.status_code == 200

    assessment = get_response.get_json()["assessment"]
    assert assessment["treatment_note"] == "已封鎖對外連線並套用修補"
    assert assessment["treatment_due_date"] == "2026-10-01"
    assert assessment["evidence_url"] == "https://example.com/evidence.png"
    assert assessment["status"] == "處理中"
    assert assessment["asset_owner"] == "alice"
    assert assessment["asset_name"] == "Web Server"


# ================================================================
# 4. 不合法的狀態更新會被拒絕
# ================================================================

def test_save_riskops_rejects_invalid_status(client, monkeypatch):
    fake = FakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client)

    response = client.post(
        "/api/risk-assessments/1/riskops",
        json={"status": "已核准"},
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert body["code"] == "INVALID_RISKOPS_STATUS"
    assert isinstance(body["error"], str) and body["error"]
    assert fake.records["risk_assessments"][0]["status"] == "待處理"


def test_save_riskops_rejects_completed_without_treatment_note(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client)

    response = client.post(
        "/api/risk-assessments/1/riskops",
        json={"status": "已完成", "treatment_note": "   "},
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert body["code"] == "TREATMENT_NOTE_REQUIRED_FOR_COMPLETION"

    # 驗證失敗時不可留下任何已變更的狀態。
    assert fake.records["risk_assessments"][0]["status"] == "待處理"


def test_save_riskops_allows_completed_with_treatment_note(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client)

    response = client.post(
        "/api/risk-assessments/1/riskops",
        json={
            "status": "已完成",
            "treatment_note": "已完成修補並驗證",
            "evidence_url": "https://example.com/evidence.png",
        },
    )

    assert response.status_code == 200
    assert fake.records["risk_assessments"][0]["status"] == "已完成"


# ================================================================
# 5. 新增風險評鑑時，原本的風險分數及 CIA 不變
# ================================================================

def test_save_assessment_preserves_risk_score_and_does_not_touch_asset_cia(
    client, monkeypatch
):
    fake = FakeSupabase(assets=[asset_record()])
    install_fake_supabase(monkeypatch, fake)
    login_as(client)

    response = client.post(
        "/api/risk-assessments/save",
        json=valid_assessment_payload(
            cvss_score=9.8,
            risk_score=49,
            risk_level="極高風險",
            impact_score=9.8,
            likelihood_score=5,
        ),
    )

    assert response.status_code == 201
    stored = fake.records["risk_assessments"][0]

    # 風險分數 / CVSS / 風險等級與送出的值完全一致，沒有被 RiskOps 欄位污染。
    assert stored["cvss_score"] == 9.8
    assert stored["risk_score"] == 49
    assert stored["risk_level"] == "極高風險"
    assert stored["impact_score"] == 9.8
    assert stored["likelihood_score"] == 5

    # 新增的 RiskOps 欄位只有 status，且是額外附加，不是覆寫既有欄位。
    assert stored["status"] == "待處理"

    # 資產本身的 CIA 完全沒有被寫入/更新過。
    assert asset_record()["confidentiality"] == fake.records["assets"][0]["confidentiality"]
    update_ops = [
        query
        for query in getattr(fake, "queries", [])
        if query.get("table") == "assets" and query.get("operation") == "update"
    ]
    assert update_ops == []


# ================================================================
# 6. 資料庫缺少必要欄位時的處理（不可讓例外外洩成 500 堆疊）
# ================================================================

def test_save_assessment_returns_clean_error_when_status_column_missing(
    client, monkeypatch
):
    fake = RaisingFakeSupabase(
        assets=[asset_record()],
        fail_table="risk_assessments",
        fail_operations=("insert",),
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client)

    response = client.post(
        "/api/risk-assessments/save",
        json=valid_assessment_payload(),
    )

    assert response.status_code == 503
    body = response.get_json()
    assert body["success"] is False
    assert body["code"] == "SAVE_ASSESSMENT_FAILED"
    # 不可洩漏原始資料庫例外內容給前端。
    assert "does not exist" not in body["error"]


def test_save_riskops_returns_clean_error_when_status_column_missing(
    client, monkeypatch
):
    fake = RaisingFakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
        fail_table="risk_assessments",
        fail_operations=("update",),
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client)

    response = client.post(
        "/api/risk-assessments/1/riskops",
        json={
            "treatment_note": "已修補",
            "status": "處理中",
        },
    )

    assert response.status_code == 503
    body = response.get_json()
    assert body["success"] is False
    assert body["code"] == "SAVE_RISKOPS_FAILED"
    assert "does not exist" not in body["error"]


# ================================================================
# 7. P0: UPDATE 回傳 0 rows 不可被誤判成功
#
# Supabase/Postgrest 的 UPDATE 在列不存在時不會丟例外，只會回傳
# `data=[]`。先前的程式碼把 `update_response.data or []` 直接當成
# 成功，即使陣列是空的 —— 等同「什麼都沒存到，卻回報成功」。
# ================================================================

def test_save_riskops_rejects_update_that_affects_zero_rows(
    client, monkeypatch
):
    fake = ZeroRowUpdateFakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
        zero_row_table="risk_assessments",
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client)

    response = client.post(
        "/api/risk-assessments/1/riskops",
        json={
            "treatment_note": "已封鎖對外連線並套用修補",
            "status": "處理中",
        },
    )

    assert response.status_code == 503
    body = response.get_json()
    assert body["success"] is False
    assert body["code"] == "RISKOPS_UPDATE_NOT_APPLIED"
    assert isinstance(body["error"], str) and body["error"]
    # 不可洩漏 Supabase 內部字樣。
    assert "supabase" not in body["error"].lower()


def test_save_riskops_returns_success_when_update_returns_matching_row(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client)

    response = client.post(
        "/api/risk-assessments/1/riskops",
        json={
            "treatment_note": "已封鎖對外連線並套用修補",
            "status": "處理中",
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert isinstance(body["data"], list) and len(body["data"]) == 1
    assert body["data"][0]["id"] == 1


def test_ai_advice_rejects_update_that_affects_zero_rows(
    client, monkeypatch
):
    fake = ZeroRowUpdateFakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
        zero_row_table="risk_assessments",
    )
    install_fake_supabase(monkeypatch, fake)

    call_count = {"n": 0}

    from riskGenie.services import risk_routes

    def fake_generate_advice(**_kwargs):
        call_count["n"] += 1
        return "請優先修補公開服務。"

    monkeypatch.setattr(risk_routes, "is_gemini_configured", lambda: True)
    monkeypatch.setattr(
        risk_routes, "generate_advice", fake_generate_advice
    )

    login_as(client)

    response = client.post(
        "/api/ai-advice",
        json=valid_ai_payload(assessment_id=1),
    )

    assert response.status_code == 503
    body = response.get_json()
    assert body["success"] is False
    assert body["code"] == "AI_ADVICE_PERSIST_FAILED"
    # Gemini 已經成功產生內容，不可丟棄 —— 前端要能把它顯示出來，
    # 即使沒有成功寫入 DB。
    assert body["advice"] == "請優先修補公開服務。"
    assert body["assessment_id"] == 1
    # 不可因為 UPDATE 失敗就重新呼叫一次 Gemini。
    assert call_count["n"] == 1


def test_ai_advice_persists_successfully_when_update_returns_matching_row(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
    )
    install_fake_supabase(monkeypatch, fake)
    enable_ai(monkeypatch)
    login_as(client)

    response = client.post(
        "/api/ai-advice",
        json=valid_ai_payload(assessment_id=1),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["assessment_id"] == 1
    assert body["advice"] == "請優先修補公開服務。"
    assert fake.records["risk_assessments"][0]["ai_suggestion"] == (
        "請優先修補公開服務。"
    )


def test_save_riskops_rejects_update_that_returns_wrong_assessment_id(
    client, monkeypatch
):
    fake = WrongRowUpdateFakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
        wrong_row_table="risk_assessments",
        wrong_row=assessment_record(999),
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client)

    response = client.post(
        "/api/risk-assessments/1/riskops",
        json={
            "treatment_note": "已封鎖對外連線並套用修補",
            "status": "處理中",
        },
    )

    assert response.status_code == 503
    body = response.get_json()
    assert body["success"] is False
    assert body["code"] == "RISKOPS_UPDATE_NOT_APPLIED"


def test_save_riskops_rejects_update_row_with_mismatched_company_id(
    client, monkeypatch
):
    fake = WrongRowUpdateFakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
        wrong_row_table="risk_assessments",
        wrong_row=assessment_record(1, company_id=99),
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.post(
        "/api/risk-assessments/1/riskops",
        json={
            "treatment_note": "已封鎖對外連線並套用修補",
            "status": "處理中",
        },
    )

    assert response.status_code == 503
    body = response.get_json()
    assert body["success"] is False
    assert body["code"] == "RISKOPS_UPDATE_NOT_APPLIED"


def test_ai_advice_rejects_update_that_returns_wrong_assessment_id(
    client, monkeypatch
):
    fake = WrongRowUpdateFakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
        wrong_row_table="risk_assessments",
        wrong_row=assessment_record(999),
    )
    install_fake_supabase(monkeypatch, fake)

    call_count = {"n": 0}

    from riskGenie.services import risk_routes

    def fake_generate_advice(**_kwargs):
        call_count["n"] += 1
        return "請優先修補公開服務。"

    monkeypatch.setattr(risk_routes, "is_gemini_configured", lambda: True)
    monkeypatch.setattr(
        risk_routes, "generate_advice", fake_generate_advice
    )

    login_as(client)

    response = client.post(
        "/api/ai-advice",
        json=valid_ai_payload(assessment_id=1),
    )

    assert response.status_code == 503
    body = response.get_json()
    assert body["success"] is False
    assert body["code"] == "AI_ADVICE_PERSIST_FAILED"
    assert body["advice"] == "請優先修補公開服務。"
    assert body["assessment_id"] == 1
    assert call_count["n"] == 1


def test_ai_advice_preview_mode_without_assessment_id_does_not_update(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1)],
    )
    install_fake_supabase(monkeypatch, fake)
    enable_ai(monkeypatch)
    login_as(client)

    response = client.post("/api/ai-advice", json=valid_ai_payload())

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["assessment_id"] is None
    assert body["advice"] == "請優先修補公開服務。"

    update_ops = [
        query
        for query in fake.queries
        if query.get("table") == "risk_assessments"
        and query.get("operation") == "update"
    ]
    assert update_ops == []


# ================================================================
# 8. Privileged client 切換：risk_assessments 走 server-side
# admin client，但租戶邊界必須完全不變。
#
# RLS 不再是 risk_assessments 這些 route 的授權邊界，所以這裡要
# 特別驗證：即使拿掉了 RLS 當最後一道防線，Flask 層的 session
# company_id + ownership 檢查仍然是唯一、且仍然有效的租戶隔離。
# ================================================================

def test_save_assessment_still_forces_session_company_with_admin_client(
    client, monkeypatch
):
    """即使前端在 payload 裡塞一個 company_id，save 出來的紀錄也
    必須是 session 的 company_id，不是前端給的那個。
    """
    fake = FakeSupabase(assets=[asset_record(company_id=7)])
    install_fake_supabase(monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.post(
        "/api/risk-assessments/save",
        json=valid_assessment_payload(company_id=99),
    )

    assert response.status_code == 201
    stored = fake.records["risk_assessments"][0]
    assert stored["company_id"] == 7


def test_save_assessment_rejects_cross_company_asset_with_admin_client(
    client, monkeypatch
):
    fake = FakeSupabase(assets=[asset_record(company_id=99)])
    install_fake_supabase(monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.post(
        "/api/risk-assessments/save",
        json=valid_assessment_payload(),
    )

    assert response.status_code == 404
    assert response.get_json()["code"] == "ASSET_NOT_FOUND"
    assert fake.records["risk_assessments"] == []


def test_get_riskops_with_admin_client_still_rejects_cross_company(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[asset_record(company_id=99)],
        assessments=[assessment_record(1, company_id=99)],
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.get("/api/risk-assessments/1/riskops")

    assert response.status_code == 404
    assert response.get_json()["code"] == "ASSESSMENT_NOT_FOUND"


def test_save_riskops_with_admin_client_still_rejects_cross_company(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[asset_record(company_id=99)],
        assessments=[assessment_record(1, company_id=99)],
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.post(
        "/api/risk-assessments/1/riskops",
        json={"treatment_note": "駭進去改的", "status": "已完成"},
    )

    assert response.status_code == 404
    assert response.get_json()["code"] == "ASSESSMENT_NOT_FOUND"
    stored = fake.records["risk_assessments"][0]
    assert stored["treatment_note"] is None


def test_ai_advice_with_admin_client_still_rejects_cross_company(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[asset_record()],
        assessments=[assessment_record(1, company_id=99, asset_id=701)],
    )
    install_fake_supabase(monkeypatch, fake)
    enable_ai(monkeypatch)
    login_as(client, company_id=7)

    response = client.post(
        "/api/ai-advice",
        json=valid_ai_payload(assessment_id=1),
    )

    assert response.status_code == 404
    assert response.get_json()["code"] == "ASSESSMENT_NOT_FOUND"
    assert fake.records["risk_assessments"][0]["ai_suggestion"] is None


def test_frontend_company_id_in_riskops_payload_does_not_change_scope(
    client, monkeypatch
):
    """save_riskops_api 的 payload 根本不接受 company_id 欄位；即使
    前端硬塞進去，scope 仍必須只看 session company_id。
    """
    fake = FakeSupabase(
        assets=[asset_record(company_id=7)],
        assessments=[assessment_record(1, company_id=7)],
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.post(
        "/api/risk-assessments/1/riskops",
        json={
            "treatment_note": "已修補",
            "status": "處理中",
            "company_id": 99,
        },
    )

    assert response.status_code == 200
    stored = fake.records["risk_assessments"][0]
    assert stored["company_id"] == 7


def test_history_list_only_returns_session_company_with_admin_client(
    client, monkeypatch
):
    fake = FakeSupabase(
        assets=[
            asset_record(asset_id=701, company_id=7),
            asset_record(asset_id=702, company_id=99),
        ],
        assessments=[
            assessment_record(1, asset_id=701, company_id=7),
            assessment_record(2, asset_id=702, company_id=99),
        ],
    )
    install_fake_supabase(monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.get("/api/risk-assessments")

    assert response.status_code == 200
    returned_ids = [
        assessment["id"]
        for assessment in response.get_json()["assessments"]
    ]
    assert returned_ids == [1]


# ================================================================
# 9. Privileged credential 不得外洩，且初始化失敗必須 fail closed
# ================================================================

def test_admin_client_builder_not_referenced_in_templates_or_static_js():
    """get_supabase_admin_client / SUPABASE_SECRET_KEY / service_role
    這些字樣絕對不可以出現在任何會送到瀏覽器的檔案裡。
    """
    forbidden = (
        "get_supabase_admin_client",
        "SUPABASE_SECRET_KEY",
        "service_role",
    )

    search_roots = []
    templates_dir = ROOT / "riskGenie" / "templates"
    static_dir = ROOT / "riskGenie" / "static"

    if templates_dir.exists():
        search_roots.append(templates_dir)
    if static_dir.exists():
        search_roots.append(static_dir)

    assert search_roots, "找不到 templates/static 目錄，測試環境有問題"

    for root in search_roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue

            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            for needle in forbidden:
                assert needle not in text, (
                    f"{needle} 不應該出現在 {path}，"
                    "privileged client 只能存在 server-side 程式碼"
                )


def test_privileged_credential_does_not_leak_into_http_response(
    client, monkeypatch
):
    """當 admin client 初始化失敗時，回應只能有乾淨的 JSON 錯誤，
    不可以把環境變數名稱、secret key 字樣洩漏到前端。
    """
    from riskGenie.services import risk_routes
    from riskGenie.services.supabase_client import SupabaseConfigError

    def fail_admin_client():
        raise SupabaseConfigError(
            "Missing required environment variable: SUPABASE_SECRET_KEY"
        )

    monkeypatch.setattr(
        risk_routes, "get_supabase_admin_client", fail_admin_client
    )
    login_as(client)

    response = client.get("/api/risk-assessments/1/riskops")

    assert response.status_code >= 500
    body_text = response.get_data(as_text=True)
    assert "SUPABASE_SECRET_KEY" not in body_text
    assert "SupabaseConfigError" not in body_text
    assert "Traceback" not in body_text


@pytest.mark.parametrize(
    "make_request",
    [
        pytest.param(
            lambda client: client.get("/api/risk-assessments/1/riskops"),
            id="get_riskops",
        ),
        pytest.param(
            lambda client: client.post(
                "/api/risk-assessments/1/riskops",
                json={"treatment_note": "x", "status": "處理中"},
            ),
            id="save_riskops",
        ),
        pytest.param(
            lambda client: client.get("/api/risk-assessments"),
            id="history_list",
        ),
        pytest.param(
            lambda client: client.post(
                "/api/risk-assessments/save",
                json=valid_assessment_payload(),
            ),
            id="save_assessment",
        ),
    ],
)
def test_privileged_client_init_failure_fails_closed(
    client, monkeypatch, make_request
):
    """admin client 初始化失敗（例如 SUPABASE_SECRET_KEY 沒設好）時，
    route 必須回報失敗，絕對不可以「反正拿不到特權 client 就當作
    沒事」而 fallback 成某種寬鬆行為。
    """
    from riskGenie.models import supabase_db
    from riskGenie.services import risk_routes
    from riskGenie.services.supabase_client import SupabaseConfigError

    fake = FakeSupabase(
        assets=[asset_record(company_id=7)],
        assessments=[assessment_record(1, company_id=7)],
    )
    # anon client 仍然正常（模擬只有 privileged key 設定有問題）。
    monkeypatch.setattr(risk_routes, "get_supabase_client", lambda: fake)
    monkeypatch.setattr(supabase_db, "get_supabase_client", lambda: fake)

    def fail_admin_client():
        raise SupabaseConfigError(
            "Missing required environment variable: SUPABASE_SECRET_KEY"
        )

    monkeypatch.setattr(
        risk_routes, "get_supabase_admin_client", fail_admin_client
    )
    monkeypatch.setattr(
        supabase_db, "get_supabase_admin_client", fail_admin_client
    )

    login_as(client, company_id=7)

    response = make_request(client)

    assert response.status_code >= 500
    body = response.get_json()
    assert body["success"] is False
