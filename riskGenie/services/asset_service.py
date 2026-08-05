# -*- coding: utf-8 -*-

from services.supabase_client import get_supabase_client


def get_assets(company_id=None):
    """
    取得資產列表
    """

    supabase = get_supabase_client()

    query = (
        supabase
        .table("assets")
        .select("*")
    )

    if company_id:
        query = query.eq(
            "company_id",
            company_id
        )

    result = (
        query
        .order("id", desc=True)
        .execute()
    )

    return result.data



def get_asset_by_id(asset_id, company_id=None):

    supabase = get_supabase_client()

    query = (
        supabase
        .table("assets")
        .select("*")
        .eq("id", asset_id)
    )

    if company_id:
        query = query.eq(
            "company_id",
            company_id
        )

    result = query.execute()

    if result.data:
        return result.data[0]

    return None



def create_asset(asset):

    supabase = get_supabase_client()

    result = (
        supabase
        .table("assets")
        .insert(asset)
        .execute()
    )

    return result.data



def update_asset(asset_id, data):

    supabase = get_supabase_client()

    result = (
        supabase
        .table("assets")
        .update(data)
        .eq("id", asset_id)
        .execute()
    )

    return result.data



def delete_asset(asset_id):

    supabase = get_supabase_client()

    result = (
        supabase
        .table("assets")
        .delete()
        .eq("id", asset_id)
        .execute()
    )

    return result.data



def check_asset_code_exists(asset_code):

    supabase = get_supabase_client()

    result = (
        supabase
        .table("assets")
        .select("id")
        .eq(
            "asset_id_code",
            asset_code
        )
        .execute()
    )

    return len(result.data) > 0