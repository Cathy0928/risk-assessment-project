# -*- coding: utf-8 -*-
"""鎖定風險公式(0-100)的單元測試。"""
import pytest

from riskGenie.services import risk_formula as rf


def calc(c, i, a, legality=0, cvss=0, weights=None):
    return rf.calculate_risk(c, i, a, legality, cvss, weights=weights)


def test_locked_formula_reference_case():
    # C5 I4 A5 -> Impact (2.0+1.2+1.5)*10 = 47;資產價值 5 -> 高 1.1;CVSS 9.8 -> critical 1.8
    r = calc(5, 4, 5, 3, 9.8)
    assert r["impact_score"] == 47.0
    assert r["label"] == "高" and r["label_mult"] == 1.1
    assert r["threat_level"] == "critical" and r["threat_mult"] == 1.8
    assert r["risk_score"] == 93.06
    assert r["risk_level"] == "極高風險"


def test_clamped_to_100():
    # 合法輸入的理論上限 = 50 x 1.1 x 1.8 = 99,不會超過 100
    r = calc(5, 5, 5, 5, 10)
    assert r["risk_score"] == 99.0
    assert 0 <= r["risk_score"] <= 100


@pytest.mark.parametrize("cvss,level,mult", [
    (0, "low", 1.0), (3.9, "low", 1.0),
    (4.0, "medium", 1.2), (6.9, "medium", 1.2),
    (7.0, "high", 1.5), (8.9, "high", 1.5),
    (9.0, "critical", 1.8), (10, "critical", 1.8),
])
def test_threat_multiplier_by_cvss_band(cvss, level, mult):
    r = calc(3, 3, 3, 0, cvss)
    assert r["threat_level"] == level and r["threat_mult"] == mult


@pytest.mark.parametrize("c,i,a,label,mult", [
    (1, 2, 2, "低", 0.9), (3, 2, 1, "中", 1.0), (4, 1, 1, "高", 1.1), (5, 5, 5, "高", 1.1),
])
def test_label_multiplier_by_asset_value(c, i, a, label, mult):
    r = calc(c, i, a)
    assert r["label"] == label and r["label_mult"] == mult


@pytest.mark.parametrize("score,level", [
    (0, "低風險"), (30, "低風險"), (30.01, "中風險"), (60, "中風險"),
    (60.01, "高風險"), (80, "高風險"), (80.01, "極高風險"), (100, "極高風險"),
])
def test_level_boundaries(score, level):
    assert rf.level_for_score(score) == level


def test_default_weights_are_locked_values():
    assert rf.DEFAULT_WEIGHTS == {"c": 0.4, "i": 0.3, "a": 0.3}
    assert calc(5, 1, 1)["weights"] == {"c": 0.4, "i": 0.3, "a": 0.3}


def test_custom_weights_change_result_and_are_normalized():
    default = calc(5, 1, 1, 0, 5)
    custom = calc(5, 1, 1, 0, 5, weights={"c": 8, "i": 1, "a": 1})  # 正規化為 0.8/0.1/0.1
    assert custom["impact_score"] == 42.0  # (0.8*5 + 0.1 + 0.1) * 10
    assert custom["impact_score"] > default["impact_score"]


def test_company_setting_only_uses_custom_weights_for_weighted_average():
    legacy = {"formula_type": "max", "weight_c": 0.9, "weight_i": 0.05, "weight_a": 0.05}
    assert rf.weights_from_settings(legacy) == rf.DEFAULT_WEIGHTS
    custom = {"formula_type": "weighted_average", "weight_c": 0.5, "weight_i": 0.25, "weight_a": 0.25}
    assert rf.weights_from_settings(custom) == {"c": 0.5, "i": 0.25, "a": 0.25}


@pytest.mark.parametrize("bad", [0, 6, -1, None, True, "x", float("nan"), float("inf")])
def test_cia_must_be_1_to_5(bad):
    with pytest.raises(rf.RiskInputError):
        calc(bad, 3, 3)


@pytest.mark.parametrize("bad", [-0.1, 10.1, "x", float("nan")])
def test_cvss_must_be_0_to_10(bad):
    with pytest.raises(rf.RiskInputError):
        calc(3, 3, 3, 0, bad)


def test_engine_compat_layer_delegates_to_single_formula():
    from riskGenie.models.risk_engine import RiskEngine
    assert RiskEngine.calculate_risk(5, 4, 5, 9.8, "max") == calc(5, 4, 5, 0, 9.8)["risk_score"]
    assert RiskEngine.get_risk_level(93.06) == "極高風險"
