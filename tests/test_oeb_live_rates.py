from decimal import Decimal
from services.oeb_live_rates import PublishedRate, tiered_energy_cost, gas_supply_cost

def rate(value):
    return PublishedRate('kWh', Decimal(value), 'November 1, 2026', 'https://www.oeb.ca', '2026-11-17T00:00:00+00:00')

def test_winter_tiered():
    assert tiered_energy_cost(Decimal('1200'), rate('11.2'), rate('13.2'), winter=True) == Decimal('138.40')

def test_summer_tiered():
    assert tiered_energy_cost(Decimal('650'), rate('11.2'), rate('13.2'), winter=False) == Decimal('73.80')

def test_gas_supply_only():
    assert gas_supply_cost(Decimal('150'), rate('9.1080')) == Decimal('13.66')
