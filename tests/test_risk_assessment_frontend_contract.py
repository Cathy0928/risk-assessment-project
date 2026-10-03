"""Regression tests for the risk_assessment.html save/calculate state machine.

Root cause being fixed:
    calculateRisk() did not require an asset to be selected, so an
    incomplete assessment could still produce a Risk Score. saveAssessment()
    then re-ran calculateRisk() instead of using the value already shown on
    screen, and the save button had no enabled/disabled state at all, so a
    successful save (and therefore a usable assessment_id for AI Advice /
    RiskOps Lite) was never guaranteed.

The JS here has no browser test runner available in this project, so the
state machine is covered with static "contract" assertions against the
rendered template source (does the right guard exist, in the right
function, before the right fetch call) plus Flask-level regression tests
confirming the backend API contracts and company isolation behaviour used
by this flow did not change.
"""

import importlib
import re
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEMPLATE_PATH = ROOT / "riskGenie" / "templates" / "risk_assessment.html"
AI_ADVICE_TEMPLATE_PATH = ROOT / "riskGenie" / "templates" / "ai_advice.html"


def _template_source():
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _ai_advice_template_source():
    return AI_ADVICE_TEMPLATE_PATH.read_text(encoding="utf-8")


def _function_body(source, function_name):
    """Extract one `function name(...) { ... }` block via brace matching.

    Only safe for functions whose string/template literals contain no
    unbalanced `{`/`}` characters (true for calculateRisk, onAssetChanged,
    invalidateCurrentResult, updateSaveButtonState and saveAssessment).
    """
    marker = f"function {function_name}("
    start = source.index(marker)
    brace_start = source.index("{", start)
    depth = 0
    for index in range(brace_start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start : index + 1]
    raise AssertionError(f"Could not find closing brace for function {function_name}")


# ===============================================================
# A. 靜態 contract test — risk_assessment.html 的狀態機
# ===============================================================


def test_save_button_has_fixed_id_and_starts_disabled():
    source = _template_source()
    start = source.index('id="saveAssessmentBtn"')
    tag_end = source.index(">", start)
    tag_text = source[max(0, start - 300) : tag_end]

    assert "disabled" in tag_text, "儲存按鈕必須預設 disabled"
    assert 'onclick="saveAssessment()"' in tag_text


def test_save_hint_element_exists_with_guidance_text():
    source = _template_source()

    assert 'id="saveHint"' in source
    assert "請先選擇資產並完成初步評鑑後再儲存" in source


def test_calculate_risk_rejects_missing_asset_before_calling_api():
    source = _template_source()
    body = _function_body(source, "calculateRisk")

    asset_check_index = body.index("assetSelect")
    fetch_index = body.index("/api/risk-assessments/calculate")

    assert asset_check_index < fetch_index, (
        "calculateRisk() 必須先驗證 assetSelect.value，才能呼叫 calculate API"
    )
    assert "if (!assetId)" in body
    assert "return null" in body


def test_calculate_risk_enables_save_button_on_success():
    source = _template_source()
    body = _function_body(source, "calculateRisk")

    assert "currentRiskData = data;" in body
    assert "updateSaveButtonState();" in body


@pytest.mark.parametrize(
    "field_id",
    [
        "confidentiality",
        "integrity",
        "availability",
        "legality",
        "cvssScore",
        "likelihoodScore",
        "threatDescription",
    ],
)
def test_risk_affecting_inputs_invalidate_current_result(field_id):
    source = _template_source()
    needle = f'id="{field_id}"'
    assert needle in source, f"找不到欄位 {field_id}"

    position = source.index(needle)
    tag_text = source[position : position + 400]

    assert 'oninput="invalidateCurrentResult()"' in tag_text, (
        f"欄位 {field_id} 修改時必須呼叫 invalidateCurrentResult()，"
        "否則舊的風險評鑑結果可能被誤認為仍然有效"
    )


def test_asset_select_change_invalidates_current_result():
    source = _template_source()

    assert 'onchange="onAssetChanged()"' in source

    body = _function_body(source, "onAssetChanged")
    assert "invalidateCurrentResult();" in body


def test_invalidate_current_result_clears_state_and_disables_save():
    source = _template_source()
    body = _function_body(source, "invalidateCurrentResult")

    assert "currentRiskData = null;" in body
    assert "currentAssessmentId = null;" in body
    assert "updateSaveButtonState();" in body


# ===============================================================
# Calculate stale-response race condition
#
# calculateRisk() 送出 request 後，使用者可能在 response 回來前
# 改了輸入（觸發 invalidateCurrentResult）、或乾脆再按一次「計算」
# 送出第二個 request。不管哪種情況，一個比較舊的 response 都絕對
# 不能在比較新的狀態之後，還反過來覆寫畫面、currentRiskData 或
# Save 按鈕狀態。這裡用一個 generation/sequence token 來擋掉。
# ===============================================================


def test_calculation_generation_token_exists_at_module_scope():
    source = _template_source()

    assert "let calculationGeneration" in source, (
        "必須有一個模組層的世代號，讓 calculateRisk() 可以判斷自己"
        "送出的 request 是否已經過期"
    )


def test_invalidate_current_result_bumps_generation():
    """使用者改輸入時，必須讓舊 request 的世代號失效，
    否則它回來時還是會被誤判成『最新的』。
    """
    source = _template_source()
    body = _function_body(source, "invalidateCurrentResult")

    assert "calculationGeneration++" in body


def test_calculate_risk_captures_generation_before_fetch():
    source = _template_source()
    body = _function_body(source, "calculateRisk")

    assert "++calculationGeneration" in body

    capture_index = body.index("++calculationGeneration")
    fetch_index = body.index("/api/risk-assessments/calculate")

    assert capture_index < fetch_index, (
        "calculateRisk() 必須在送出 request 之前，先捕捉當時的"
        "世代號，才能在 response 回來後判斷是否過期"
    )


def test_calculate_risk_checks_stale_generation_after_await():
    """await fetch 完成後，必須先確認世代號沒有變，才可以繼續
    處理這個 response（包含判斷 success/error）。
    """
    source = _template_source()
    body = _function_body(source, "calculateRisk")

    response_json_index = body.index("await response.json();")
    stale_guard_index = body.index(
        "requestGeneration !== calculationGeneration"
    )
    error_check_index = body.index("!response.ok || !data.success")

    assert response_json_index < stale_guard_index < error_check_index, (
        "stale generation 的檢查必須在拿到 response 之後、"
        "判斷 success/error 之前就先擋下來"
    )


def test_calculate_risk_stale_response_does_not_write_current_risk_data():
    source = _template_source()
    body = _function_body(source, "calculateRisk")

    stale_guard_index = body.index(
        "requestGeneration !== calculationGeneration"
    )
    stale_return_index = body.index("return null;", stale_guard_index)
    assign_index = body.index("currentRiskData = data;")

    assert stale_return_index < assign_index, (
        "stale response 必須在觸碰 currentRiskData 之前就 return，"
        "不可以用過期的計算結果覆寫目前畫面上的評鑑結果"
    )


def test_calculate_risk_stale_response_does_not_enable_save_button():
    source = _template_source()
    body = _function_body(source, "calculateRisk")

    stale_guard_index = body.index(
        "requestGeneration !== calculationGeneration"
    )
    stale_return_index = body.index("return null;", stale_guard_index)
    assign_index = body.index("currentRiskData = data;")
    success_save_update_index = body.index(
        "updateSaveButtonState();", assign_index
    )

    assert stale_return_index < success_save_update_index, (
        "stale response 必須在成功分支重新啟用 Save 按鈕之前就"
        "return，不可以讓過期的計算結果把 Save 按鈕打開"
    )


def test_update_save_button_state_disables_without_asset_or_result():
    source = _template_source()
    body = _function_body(source, "updateSaveButtonState")

    assert "saveBtn.disabled = true;" in body
    assert "saveBtn.disabled = false;" in body


def test_save_assessment_does_not_call_calculate_again():
    source = _template_source()
    body = _function_body(source, "saveAssessment")

    assert "calculateRisk(" not in body, (
        "saveAssessment() 不應該再次呼叫 calculateRisk()，"
        "必須直接使用畫面上已確認的 currentRiskData"
    )
    assert "if (!currentRiskData)" in body


def test_save_assessment_uses_current_risk_data_for_payload():
    source = _template_source()
    body = _function_body(source, "saveAssessment")

    assert "currentRiskData.impact_score" in body
    assert "currentRiskData.risk_score" in body
    assert "currentRiskData.risk_level" in body


def test_save_assessment_guards_against_double_submit():
    source = _template_source()
    body = _function_body(source, "saveAssessment")

    disable_index = body.index("saveBtn.disabled = true;")
    fetch_index = body.index("/api/risk-assessments/save")

    assert disable_index < fetch_index, "送出前必須先 disable 儲存按鈕，避免重複送出"


def test_save_assessment_only_trusts_top_level_assessment_id():
    """save 成功後只接受 backend 回傳的 top-level assessment_id，
    且必須驗證為合法正整數，否則視為儲存失敗（不可用舊的
    data.data[0].id 寫法，那不保證一定存在/合法）。
    """
    source = _template_source()
    body = _function_body(source, "saveAssessment")

    assert "data.assessment_id" in body
    assert "Number.isInteger(" in body
    assert "data.data[0].id" not in body
    assert "currentAssessmentId =" in body
    assert "assessmentId;" in body


def test_save_assessment_keeps_current_result_on_failure():
    source = _template_source()
    body = _function_body(source, "saveAssessment")

    # 失敗時不能清掉 currentRiskData，讓使用者可以修正後直接重試。
    assert "currentRiskData = null;" not in body


def test_save_assessment_re_enables_button_via_shared_state_updater():
    source = _template_source()
    body = _function_body(source, "saveAssessment")

    finally_index = body.index("} finally {")
    tail = body[finally_index:]

    assert "updateSaveButtonState();" in tail


def test_ai_advice_flow_reuses_existing_assessment_id_state_only():
    """B 要求：不要為 AI Advice 另外發明第二套 assessment_id state。"""
    source = _template_source()

    assert source.count("let currentAssessmentId") == 1
    assert re.search(r"assessment_id:\s*currentAssessmentId", source)


def test_ai_preview_warning_element_exists_with_clear_message():
    source = _template_source()

    assert 'id="aiPreviewWarning"' in source
    assert "無法使用 RiskOps" in source


def test_opening_ai_modal_shows_preview_warning_when_no_assessment_id():
    """沒有 currentAssessmentId（尚未成功儲存）時，使用者一打開
    AI 建議 modal 就必須看到明確提示，不能等到 RiskOps 才卡住。
    """
    source = _template_source()
    body = _function_body(source, "openAIAdviceModal")

    assert "aiPreviewWarning" in body
    assert "currentAssessmentId" in body
    assert "? 'none'" in body
    assert ": 'block'" in body


# ===============================================================
# E. P0 persistence failure contract — 前端呈現
#
# AI Advice 和 RiskOps 的 UPDATE 現在都可能回報「0-row 更新」當作
# 明確失敗（AI_ADVICE_PERSIST_FAILED / RISKOPS_UPDATE_NOT_APPLIED）。
# 前端不可以把這種情況當成一般成功，也不可以把已經生成的 advice
# 文字丟掉，或清空使用者已輸入的 RiskOps 欄位。
# ===============================================================


def test_confirm_ai_advice_handles_persist_failed_before_generic_throw():
    """AI_ADVICE_PERSIST_FAILED 必須被特別處理，不能falls through
    到『throw new Error(...)』那條一般失敗路徑，否則已生成的
    advice 文字會被直接丟掉。
    """
    source = _template_source()
    body = _function_body(source, "confirmAIAdvice")

    assert "AI_ADVICE_PERSIST_FAILED" in body

    persist_failed_index = body.index("AI_ADVICE_PERSIST_FAILED")
    generic_throw_index = body.index("throw new Error(")

    assert persist_failed_index < generic_throw_index, (
        "AI_ADVICE_PERSIST_FAILED 的特殊處理必須排在一般 throw 之前，"
        "否則會被當成普通錯誤處理，advice 內容就不見了"
    )


def test_confirm_ai_advice_keeps_advice_text_on_persist_failed():
    source = _template_source()
    body = _function_body(source, "confirmAIAdvice")

    persist_failed_index = body.index("AI_ADVICE_PERSIST_FAILED")
    redirect_index = body.index("window.location.href")

    # 保存失敗時絕對不能跳轉去 AI 建議頁（那會讓人誤以為已經存好）。
    assert persist_failed_index < redirect_index

    branch = body[persist_failed_index:redirect_index]

    assert "data.advice" in branch, (
        "AI 建議已產生但未保存時，必須把 data.advice 顯示出來，"
        "不能讓使用者以為內容整個不見了"
    )
    assert "return;" in branch, (
        "處理完 AI_ADVICE_PERSIST_FAILED 後必須 return，"
        "不能繼續往下跑到成功的 sessionStorage / 跳轉邏輯"
    )


def test_confirm_ai_advice_persist_failed_does_not_claim_success():
    source = _template_source()
    body = _function_body(source, "confirmAIAdvice")

    persist_failed_index = body.index("AI_ADVICE_PERSIST_FAILED")
    return_index = body.index("return;", persist_failed_index)
    branch = body[persist_failed_index:return_index]

    # 這個分支不可以把 success 寫成 true 之類的字樣誤導使用者。
    assert "success:\n                true" not in branch
    assert "success:true" not in branch.replace(" ", "")


def test_save_riskops_fails_closed_on_backend_error_code():
    source = _ai_advice_template_source()
    body = _function_body(source, "saveRiskOps")

    guard_index = body.index(
        "!response.ok || !result.success"
    )
    fetch_index = body.index(
        "/api/risk-assessments/${assessmentId}/riskops"
    )
    success_message_index = body.index("儲存成功")

    assert fetch_index < guard_index < success_message_index, (
        "saveRiskOps() 必須先檢查 result.success 再顯示成功訊息，"
        "不可以讓 RISKOPS_UPDATE_NOT_APPLIED 這種 success:false 的"
        "回應被當成『RiskOps Lite 儲存成功』顯示出來"
    )


def test_save_riskops_failure_path_does_not_clear_form_fields():
    source = _ai_advice_template_source()
    body = _function_body(source, "saveRiskOps")

    catch_index = body.index("} catch (error) {")
    tail = body[catch_index:]

    # 失敗分支只能寫訊息到 message.textContent，不可以去動
    # treatmentNote / treatmentDueDate / evidenceUrl / riskOpsStatus
    # 這些輸入欄位的 .value，否則使用者剛打的內容會憑空消失。
    for field_id in (
        "treatmentNote",
        "treatmentDueDate",
        "evidenceUrl",
        "riskOpsStatus",
    ):
        assert f"'{field_id}').value =" not in tail, (
            f"saveRiskOps() 失敗處理不應該清空 {field_id} 的內容"
        )


# ===============================================================
# D.6 / D.7 — Backend 迴歸測試：
# company isolation 不退化、calculate/save API contract 不變
# ===============================================================


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


def login_as(client, company_id=7):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = "user-id"
        sess["username"] = "Peggy"
        sess["email"] = "peggy@example.com"
        sess["role_name"] = "user"
        sess["company_id"] = company_id


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.filters = []
        self.limit_value = None
        self.operation = "select"
        self.insert_payload = None

    def select(self, fields):
        self.operation = "select"
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.insert_payload = deepcopy(payload)
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def in_(self, field, values):
        self.filters.append((field, list(values)))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def _matches(self, record):
        for field, value in self.filters:
            stored = record.get(field)
            if isinstance(value, list):
                if stored not in value:
                    return False
            elif stored != value:
                return False
        return True

    def execute(self):
        if self.operation == "insert":
            inserted = deepcopy(self.insert_payload)
            inserted.setdefault("id", 9001)
            self.client.records.setdefault(self.table_name, []).append(inserted)
            return SimpleNamespace(data=[inserted])

        records = [
            record.copy()
            for record in self.client.records.get(self.table_name, [])
            if self._matches(record)
        ]
        if self.limit_value is not None:
            records = records[: self.limit_value]
        return SimpleNamespace(data=records)


class FakeSupabase:
    def __init__(self, assets=None, assessments=None):
        self.records = {
            "assets": list(assets or []),
            "risk_assessments": list(assessments or []),
            "audit_logs": [],
        }

    def table(self, table_name):
        return FakeQuery(self, table_name)


def install_fake_supabase(monkeypatch, assets=None, assessments=None):
    from riskGenie.models import supabase_db
    from riskGenie.services import risk_routes

    fake = FakeSupabase(assets=assets, assessments=assessments)
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
        "is_deleted": False,
    }
    record.update(overrides)
    return record


def calculate_payload(**overrides):
    payload = {
        "confidentiality": 5,
        "integrity": 4,
        "availability": 5,
        "legality": 3,
        "cvss_score": 9.8,
        "likelihood_score": 5,
    }
    payload.update(overrides)
    return payload


def save_payload(**overrides):
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


def test_calculate_api_contract_is_unchanged(client, monkeypatch):
    """calculate 端點本身不認識 asset_id；資產必選是前端的把關，
    因此這裡只需確認後端 contract（欄位與行為）維持不變。
    """
    from riskGenie.services import risk_routes

    monkeypatch.setattr(
        risk_routes.RiskService,
        "get_weight_settings",
        lambda company_id: {
            "company_id": company_id,
            "formula_type": "max",
            "weight_c": 0.3333,
            "weight_i": 0.3333,
            "weight_a": 0.3333,
        },
    )
    login_as(client)

    response = client.post(
        "/api/risk-assessments/calculate",
        json=calculate_payload(),
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert set(
        ["impact_score", "risk_score", "risk_level", "formula_used"]
    ).issubset(data.keys())


def test_save_api_contract_is_unchanged_and_returns_assessment_id(
    client, monkeypatch
):
    """saveAssessment() 現在直接拿 currentRiskData 當 save payload，
    欄位形狀必須與既有後端 contract 一致，且回應必須在 top-level
    附上合法的 assessment_id，與 insert row 的 id 一致。
    """
    fake = install_fake_supabase(monkeypatch, assets=[asset_record()])
    login_as(client)

    response = client.post(
        "/api/risk-assessments/save",
        json=save_payload(),
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["success"] is True
    assert data["assessment_id"] == 9001
    assert data["data"][0]["id"] == 9001
    assert data["assessment_id"] == data["data"][0]["id"]
    assert fake.records["risk_assessments"][0]["company_id"] == 7


def test_save_writes_timezone_aware_assessment_timestamp(client, monkeypatch):
    """The persisted instant must never be a naive server-local datetime.

    A naive Asia/Taipei wall-clock value stored as UTC is the source of the
    historical-page +8 hour regression.
    """
    fake = install_fake_supabase(monkeypatch, assets=[asset_record()])
    login_as(client)

    response = client.post(
        "/api/risk-assessments/save",
        json=save_payload(),
    )

    assert response.status_code == 201
    timestamp = fake.records["risk_assessments"][0]["created_at"]
    parsed = datetime.fromisoformat(timestamp)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None


def _as_taipei_time(timestamp):
    """Model the single local conversion performed by the browser."""
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return parsed.astimezone(timezone(timedelta(hours=8)))


def test_ai_advice_timestamp_with_offset_is_not_shifted_twice():
    rendered = _as_taipei_time(
        "2026-10-03T15:34:41+08:00"
    )

    assert rendered.hour == 15
    assert rendered.minute == 34


def test_ai_advice_utc_timestamp_is_converted_to_taipei_once():
    offset_time = _as_taipei_time(
        "2026-10-03T15:34:41+08:00",
    )
    utc_time = _as_taipei_time(
        "2026-10-03T07:34:41Z",
    )

    assert utc_time == offset_time
    assert utc_time.hour == 15


def test_historical_reopen_does_not_mislabel_assessment_created_at_as_ai_time():
    source = _ai_advice_template_source()

    assert not re.search(r"generated_at:\s*assessment\.created_at", source)
    assert re.search(r"assessment_created_at:\s*assessment\.created_at", source)
    assert 'id="generatedTimeLabel"' in source
    assert "'AI 建議產生時間'" in source
    assert "'評鑑建立時間'" in source
    assert "data.generated_at ||" in source
    assert "data.assessment_created_at" in source
    assert ".toLocaleString(" in source


def test_save_rejects_when_insert_result_is_empty(client, monkeypatch):
    """insert 看起來成功但 Supabase 沒有回傳任何 row，
    不可以回 201 success（避免前端拿到一個不存在的評鑑紀錄）。
    """
    from riskGenie.services import risk_routes

    monkeypatch.setattr(
        risk_routes,
        "save_risk_assessment_record",
        lambda *_args, **_kwargs: [],
    )
    login_as(client)

    response = client.post(
        "/api/risk-assessments/save",
        json=save_payload(),
    )

    assert response.status_code == 503
    data = response.get_json()
    assert data["success"] is False
    assert data["code"] == "SAVE_ASSESSMENT_FAILED"


def test_save_rejects_when_insert_row_missing_id(client, monkeypatch):
    from riskGenie.services import risk_routes

    monkeypatch.setattr(
        risk_routes,
        "save_risk_assessment_record",
        lambda *_args, **_kwargs: [{"company_id": 7, "asset_id": 701}],
    )
    login_as(client)

    response = client.post(
        "/api/risk-assessments/save",
        json=save_payload(),
    )

    assert response.status_code == 503
    assert response.get_json()["code"] == "SAVE_ASSESSMENT_FAILED"


@pytest.mark.parametrize(
    "bad_id",
    [0, -1, "not-a-number", None, True, False, [], {}],
)
def test_save_rejects_illegal_assessment_id(client, monkeypatch, bad_id):
    """非法 id（非正整數、布林值、無法轉換的字串、容器型別）
    一律視為儲存失敗，不可以回 201。
    """
    from riskGenie.services import risk_routes

    monkeypatch.setattr(
        risk_routes,
        "save_risk_assessment_record",
        lambda *_args, **_kwargs: [{"id": bad_id}],
    )
    login_as(client)

    response = client.post(
        "/api/risk-assessments/save",
        json=save_payload(),
    )

    assert response.status_code == 503
    data = response.get_json()
    assert data["success"] is False
    assert data["code"] == "SAVE_ASSESSMENT_FAILED"
    # 不可以洩漏內部 Supabase / exception 細節。
    assert "Traceback" not in data["error"]
    assert "supabase" not in data["error"].lower()


def test_save_accepts_numeric_string_id_that_converts_to_positive_int(
    client, monkeypatch
):
    """Supabase 有時會把 bigint id 序列化成字串；只要能安全轉成
    合法正整數，仍應視為成功（對應需求：『可轉為整數』）。
    """
    from riskGenie.services import risk_routes

    # 成功路徑會接著嘗試寫入 audit_logs，所以仍要提供一個假的
    # Supabase client，避免真的打網路。
    install_fake_supabase(monkeypatch)
    monkeypatch.setattr(
        risk_routes,
        "save_risk_assessment_record",
        lambda *_args, **_kwargs: [{"id": "42"}],
    )
    login_as(client)

    response = client.post(
        "/api/risk-assessments/save",
        json=save_payload(),
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["success"] is True
    assert data["assessment_id"] == 42


def test_save_still_rejects_cross_company_asset(client, monkeypatch):
    """Company isolation 迴歸守門：這次只改前端狀態機，
    跨公司資產仍必須被後端擋下，不能因為前端改動而退化。
    """
    install_fake_supabase(monkeypatch, assets=[asset_record(company_id=99)])
    login_as(client, company_id=7)

    response = client.post(
        "/api/risk-assessments/save",
        json=save_payload(),
    )

    assert response.status_code == 404
    assert response.get_json()["code"] == "ASSET_NOT_FOUND"


def test_historical_assessments_remain_scoped_to_session_company(
    client, monkeypatch
):
    install_fake_supabase(
        monkeypatch,
        assets=[
            asset_record(asset_id=701, company_id=7),
            asset_record(asset_id=702, company_id=99),
        ],
        assessments=[
            {
                "id": 1,
                "company_id": 7,
                "asset_id": 701,
                "risk_level": "高風險",
                "created_at": "2026-09-01T00:00:00+00:00",
            },
            {
                "id": 2,
                "company_id": 99,
                "asset_id": 702,
                "risk_level": "極高風險",
                "created_at": "2026-09-02T00:00:00+00:00",
            },
        ],
    )
    login_as(client, company_id=7)

    response = client.get("/api/risk-assessments")

    assert response.status_code == 200
    data = response.get_json()
    returned_ids = [assessment["id"] for assessment in data["assessments"]]
    assert returned_ids == [1]
