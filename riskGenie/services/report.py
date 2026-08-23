from io import BytesIO

from flask import send_file
from openpyxl import Workbook


REPORT_COLUMNS = (
    "資產代碼",
    "資產名稱",
    "資產類型",
    "評鑑編號",
    "CVSS",
    "可能性",
    "衝擊分數",
    "風險分數",
    "風險等級",
    "評鑑時間",
)


def _company_id_matches(record, company_id):
    value = record.get("company_id")
    if isinstance(value, bool):
        return False

    try:
        return int(value) == company_id
    except (TypeError, ValueError):
        return False


def _excel_safe(value):
    if not isinstance(value, str):
        return value

    stripped = value.lstrip()
    if stripped.startswith(("=", "+", "-", "@")):
        return "'" + value

    return value


def export_report(assets, assessments, company_id):
    """Create an in-memory, tenant-scoped risk report workbook."""
    scoped_assets = [
        asset
        for asset in assets
        if _company_id_matches(asset, company_id)
        and not asset.get("is_deleted", False)
    ]
    asset_map = {
        asset.get("id"): asset
        for asset in scoped_assets
        if asset.get("id") is not None
    }
    scoped_assessments = [
        assessment
        for assessment in assessments
        if _company_id_matches(assessment, company_id)
        and assessment.get("asset_id") in asset_map
    ]

    assessments_by_asset = {}
    for assessment in scoped_assessments:
        assessments_by_asset.setdefault(
            assessment.get("asset_id"),
            []
        ).append(assessment)

    wb = Workbook()
    ws = wb.active
    ws.title = "風險報表"
    ws.append(REPORT_COLUMNS)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:J1"

    for asset in scoped_assets:
        asset_assessments = assessments_by_asset.get(
            asset.get("id"),
            []
        ) or [None]

        for assessment in asset_assessments:
            assessment = assessment or {}
            ws.append((
                _excel_safe(asset.get("asset_id_code", "")),
                _excel_safe(asset.get("asset_name", "")),
                _excel_safe(asset.get("asset_type", "")),
                assessment.get("id", ""),
                assessment.get("cvss_score", ""),
                assessment.get("likelihood_score", ""),
                assessment.get("impact_score", ""),
                assessment.get("risk_score", ""),
                _excel_safe(assessment.get("risk_level", "")),
                _excel_safe(assessment.get("created_at", "")),
            ))

    output = BytesIO()
    wb.save(output)
    wb.close()
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="Risk_Report.xlsx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )
