from decimal import Decimal

import pytest

from services.property_cost_estimator import EstimationError, estimate_costs, normalize_ontario_postal


def test_postal_validation():
    assert normalize_ontario_postal("m5v2t6") == "M5V 2T6"
    with pytest.raises(EstimationError):
        normalize_ontario_postal("90210")


def test_toronto_estimates_are_decimal_and_selective():
    estimates = estimate_costs(
        city="Toronto", postal_code="M5V 2T6", purchase_price=Decimal("675000"),
        electricity_kwh=Decimal("650"), gas_m3=Decimal("150"), water_m3=Decimal("20"),
        uses_natural_gas=False, water_billed_separately=True,
        include_insurance=True, include_repair_reserve=True,
    )
    amounts = {item.field: item.amount for item in estimates}
    assert amounts["electricity"] == Decimal("130.00")
    assert amounts["water"] == Decimal("97.26")
    assert amounts["repair_reserve"] == Decimal("562.50")
    assert "natural_gas" not in amounts


def test_no_non_toronto_water_rate_invented():
    estimates = estimate_costs(
        city="Ottawa", postal_code="K1A 0B1", purchase_price=None,
        electricity_kwh=Decimal("100"), gas_m3=Decimal("0"), water_m3=Decimal("20"),
        uses_natural_gas=False, water_billed_separately=True,
        include_insurance=False, include_repair_reserve=False,
    )
    assert {item.field for item in estimates} == {"electricity"}
