# -*- coding: utf-8 -*-
"""
CIA AI-rating human-review Blueprint (new, additive).

Flow implemented here:
    既有資產 (existing asset, scoped by company_id)
        -> Asset Context Construction (cia_rating_service.build_asset_context)
        -> CIA Prompt (cia_rating_service.CIA_PROMPT_TEMPLATE)
        -> Gemini (cia_rating_service.generate_cia_suggestion)
        -> Structured JSON + Validation
        -> Display AI CIA Suggestion (asset_cia_review.html)
        -> Human Review (explicit "採用AI建議" / "維持人工值" action per
           dimension -> /asset_cia_review/<id>/adopt)

This module never overwrites confidentiality/integrity/availability on its
own. The `assets` table is only updated inside adopt_cia_suggestion(), and
only for the single dimension the human explicitly chose to adopt.
"""

import logging
import sys
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    from .cia_rating_service import (
        CIA_DIMENSIONS,
        generate_cia_suggestion,
    )
    from .supabase_client import get_supabase_client
except ImportError:
    from services.cia_rating_service import (
        CIA_DIMENSIONS,
        generate_cia_suggestion,
    )
    from services.supabase_client import get_supabase_client

from evaluation.cia_eval_logger import log_cia_run

cia_bp = Blueprint("cia", __name__)
logger = logging.getLogger(__name__)

_DIMENSION_LABELS = {
    "confidentiality": "機密性 (C)",
    "integrity": "完整性 (I)",
    "availability": "可用性 (A)",
}


def _session_company_id():
    company_id = session.get("company_id")
    try:
        if company_id is None:
            return None
        company_id = int(company_id)
    except (TypeError, ValueError):
        return None
    return company_id if company_id > 0 else None


def _get_scoped_asset(asset_id, company_id):
    supabase = get_supabase_client()
    response = (
        supabase
        .table("assets")
        .select("*")
        .eq("id", asset_id)
        .eq("company_id", company_id)
        .limit(1)
        .execute()
    )
    data = getattr(response, "data", None) or []
    return data[0] if data else None


def _session_key(asset_id):
    return f"cia_suggestion_{asset_id}"


@cia_bp.route("/asset_cia_review/<int:asset_id>", methods=["GET"])
def cia_review_page(asset_id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    company_id = _session_company_id()
    if company_id is None:
        return "帳號缺少公司識別資訊", 403

    asset = _get_scoped_asset(asset_id, company_id)
    if not asset:
        return "找不到資產", 404

    ai_suggestion = session.get(_session_key(asset_id))

    return render_template(
        "asset_cia_review.html",
        asset=asset,
        asset_id=asset_id,
        ai_suggestion=ai_suggestion,
        dimension_labels=_DIMENSION_LABELS,
    )


@cia_bp.route("/asset_cia_review/<int:asset_id>/generate", methods=["POST"])
def cia_review_generate(asset_id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    company_id = _session_company_id()
    if company_id is None:
        return "帳號缺少公司識別資訊", 403

    asset = _get_scoped_asset(asset_id, company_id)
    if not asset:
        return "找不到資產", 404

    result = generate_cia_suggestion(asset)

    try:
        log_cia_run(asset=asset, result=result)
    except Exception:
        logger.exception("Failed to write CIA evaluation CSV row (asset_id=%s)", asset_id)

    if result.success:
        session[_session_key(asset_id)] = {
            "success": True,
            "data": result.data,
            "model_id": result.model_id,
            "temperature": result.temperature,
            "prompt_version": result.prompt_version,
            "retry_count": result.retry_count,
        }
        error_message = None
    else:
        session.pop(_session_key(asset_id), None)
        error_message = f"AI 建議產生失敗（{result.error_type}），請稍後再試。"

    return render_template(
        "asset_cia_review.html",
        asset=asset,
        asset_id=asset_id,
        ai_suggestion=session.get(_session_key(asset_id)),
        dimension_labels=_DIMENSION_LABELS,
        error=error_message,
    )


@cia_bp.route("/asset_cia_review/<int:asset_id>/adopt", methods=["POST"])
def cia_review_adopt(asset_id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    company_id = _session_company_id()
    if company_id is None:
        return "帳號缺少公司識別資訊", 403

    dimension = request.form.get("dimension")
    action = request.form.get("action")

    if dimension not in CIA_DIMENSIONS or action not in ("adopt", "keep"):
        return "無效的請求", 400

    asset = _get_scoped_asset(asset_id, company_id)
    if not asset:
        return "找不到資產", 404

    if action == "adopt":
        suggestion = session.get(_session_key(asset_id))
        if not suggestion or not suggestion.get("success"):
            return "沒有可採用的 AI 建議", 400

        rating = suggestion["data"].get(dimension, {}).get("rating")
        if not isinstance(rating, int):
            return "AI 建議資料無效", 400

        # Recompute asset_value using the SAME max(C,I,A,L) formula already
        # used by asset_add/asset_edit in riskGenie/app.py, so this write
        # does not leave the existing derived asset_value column stale.
        # This reuses the existing formula; it does not change RiskEngine.
        cia_values = {
            "confidentiality": asset.get("confidentiality"),
            "integrity": asset.get("integrity"),
            "availability": asset.get("availability"),
        }
        cia_values[dimension] = rating
        legality = asset.get("legality")
        numeric_values = [
            v for v in list(cia_values.values()) + [legality]
            if isinstance(v, int)
        ]
        update_payload = {dimension: rating}
        if numeric_values:
            update_payload["asset_value"] = max(numeric_values)

        supabase = get_supabase_client()
        supabase.table("assets").update(
            update_payload
        ).eq("id", asset_id).eq("company_id", company_id).execute()

        logger.info(
            "CIA suggestion adopted: asset_id=%s dimension=%s value=%s",
            asset_id,
            dimension,
            rating,
        )

    # "keep" performs no write -- the human explicitly kept the original value.

    return redirect(url_for("cia.cia_review_page", asset_id=asset_id))
