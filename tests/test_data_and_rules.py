import pytest

from emporio_ai.business_rules import BusinessRules, installment_plan, max_interest_free_installments, pix_price
from emporio_ai.data_store import DataLoadError, DataStore
from emporio_ai.text_utils import brl, detect_language, extract_money_values, parse_amount

RULES = BusinessRules()


def test_sample_dataset_loads_and_reports_quality_issues(agent):
    report = agent.store.report
    assert report.row_counts["products"] == 15
    assert any("no start/end dates" in issue for issue in report.issues)
    assert any("more than one active promotion" in issue for issue in report.issues)


def test_missing_dataset_raises_clear_error(tmp_path):
    with pytest.raises(DataLoadError):
        DataStore(tmp_path)


def test_missing_required_column_is_rejected(tmp_path, agent):
    import shutil

    for f in agent.settings.data_dir.glob("*.csv"):
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "products.csv").write_text("product_id,name\n1,X\n", encoding="utf-8")
    with pytest.raises(DataLoadError, match="missing columns"):
        DataStore(tmp_path)


@pytest.mark.parametrize("total,expected", [(40, 1), (150, 3), (480, 6), (599.9, 6), (700, 7), (2199, 12), (1150, 11)])
def test_installment_tiers(total, expected):
    assert max_interest_free_installments(total, RULES) == expected


def test_installment_value_never_exceeds_total():
    plan = installment_plan(599.9, RULES)
    assert plan["installments"] * plan["installment_value"] <= 599.9


def test_pix_discount_is_not_cumulative_with_promotions():
    assert pix_price(1000, has_promotion=False, rules=RULES) == 950.0
    assert pix_price(1000, has_promotion=True, rules=RULES) is None


def test_only_the_best_active_promotion_is_applied(agent):
    view = agent.catalog.product_view(204)  # two active promotions: 15% and 5%
    assert view["promotion"]["discount_percent"] == 15
    assert view["final_price"] == pytest.approx(2116.5)
    assert "pix_price" in view and view["pix_price"] is None


def test_inactive_promotions_are_never_applied(agent):
    view = agent.catalog.product_view(209)  # only an inactive Black Friday promotion
    assert "promotion" not in view
    assert view["final_price"] == view["list_price"] == 420


def test_text_helpers():
    assert brl(1234.5) == "R$ 1.234,50"
    assert parse_amount("1.000") == 1000
    assert parse_amount("1,5 mil") == 1500
    assert parse_amount("2k") == 2000
    assert extract_money_values("De R$ 5.999,00 por R$ 5.519,08.") == [5999.0, 5519.08]
    assert detect_language("How much is the guitar?") == "en"
    assert detect_language("Quanto custa o violão?") == "pt"


def test_business_rules_are_found_in_policy_document(agent):
    assert agent.policies.missing_rule_anchors == []
