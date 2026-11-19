"""Transparent Ontario property-cost estimates. No Streamlit or database imports.

Only Toronto's configured volumetric water rate is treated as a published tariff.
All other amounts are explicitly editable planning assumptions, not utility bills.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "utility_estimation_rules.toml"
POSTAL_RE = re.compile(r"^[KLMNP][0-9][A-Z] ?[0-9][A-Z][0-9]$")
CENT = Decimal("0.01")


class EstimationError(ValueError):
    pass


def normalize_ontario_postal(value: str) -> str:
    code = re.sub(r"\s+", "", (value or "").upper())
    if not POSTAL_RE.fullmatch(code):
        raise EstimationError("Enter a valid Ontario postal code (for example, M5V 2T6).")
    return code[:3] + " " + code[3:]


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def read_config(path: Path = DEFAULT_CONFIG) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


@dataclass(frozen=True)
class Estimate:
    field: str
    amount: Decimal
    basis: str
    source: str


def lookup_postal(postal_code: str, *, timeout: float = 5.0) -> dict | None:
    """Best-effort free OSM lookup. Not a definitive postal/address verification.

    Call only from a user-triggered action; respect Nominatim's usage policy.
    """
    postal = normalize_ontario_postal(postal_code)
    params = urlencode({
        "postalcode": postal, "country": "Canada", "format": "jsonv2",
        "addressdetails": 1, "limit": 1,
    })
    url = "https://nominatim.openstreetmap.org/search?" + params
    req = Request(url, headers={
        "User-Agent": "OntarioHomeBudget/1.0 (personal, low-volume postal lookup)",
        "Accept": "application/json",
    })
    try:
        with urlopen(req, timeout=timeout) as response:
            rows = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None
    if not rows:
        return None
    address = rows[0].get("address") or {}
    returned_postal = address.get("postcode")
    if returned_postal:
        try:
            if normalize_ontario_postal(returned_postal) != postal:
                return None
        except EstimationError:
            return None
    return {
        "display_name": rows[0].get("display_name", ""),
        "city": address.get("city") or address.get("town") or address.get("municipality") or address.get("village"),
        "postcode_confirmed": bool(returned_postal),
    }


def estimate_costs(
    *, city: str, postal_code: str, purchase_price: Decimal | None,
    electricity_kwh: Decimal, gas_m3: Decimal, water_m3: Decimal,
    uses_natural_gas: bool, water_billed_separately: bool,
    include_insurance: bool, include_repair_reserve: bool,
    config: dict | None = None,
) -> list[Estimate]:
    normalize_ontario_postal(postal_code)
    cfg = config if config is not None else read_config()
    a = cfg["assumptions"]
    for name, value in (("Electricity", electricity_kwh), ("Natural gas", gas_m3), ("Water", water_m3)):
        if not value.is_finite() or value < 0:
            raise EstimationError(f"{name} consumption must be a non-negative finite number.")
    out = [Estimate(
        "electricity", money(electricity_kwh * Decimal(str(a["electricity_all_in_per_kwh"]))),
        f"{electricity_kwh} kWh/month × illustrative blended CAD/kWh rate",
        "Editable planning assumption; confirm local distributor tariff and fixed fees",
    )]
    if uses_natural_gas:
        out.append(Estimate(
            "natural_gas", money(gas_m3 * Decimal(str(a["natural_gas_all_in_per_m3"]))),
            f"{gas_m3} m³/month × illustrative blended CAD/m³ rate",
            "Editable planning assumption; confirm gas distributor, fixed fees and carbon charges",
        ))
    if water_billed_separately and (city or "").strip().casefold() == "toronto":
        water_cfg = cfg["toronto_water"]
        out.append(Estimate(
            "water", money(water_m3 * Decimal(str(water_cfg["rate_per_m3"]))),
            f"{water_m3} m³/month × configured Toronto water/wastewater volumetric rate",
            water_cfg["source"],
        ))
    if include_insurance:
        out.append(Estimate(
            "home_insurance", money(Decimal(str(a["annual_home_insurance"])) / 12),
            "Illustrative annual home insurance budget ÷ 12",
            "Planning assumption; request an address-specific insurer quote",
        ))
    if include_repair_reserve and purchase_price is not None:
        price = Decimal(str(purchase_price))
        if not price.is_finite() or price < 0:
            raise EstimationError("Purchase price must be non-negative and finite.")
        out.append(Estimate(
            "repair_reserve", money(price * Decimal(str(a["annual_repair_reserve_pct"])) / 1200),
            "Purchase price × annual maintenance reserve percentage ÷ 12",
            "Planning heuristic; adjust for condition, age and strata/condo coverage",
        ))
    return out
