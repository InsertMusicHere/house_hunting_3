"""Loads and validates config/financial_rules.toml.

No rate, bracket, rebate or limit is written in code: the calculator reads them from this
module's Rules object. A malformed rules file raises RulesError with a plain-language message
rather than letting a bad number flow into a result.
"""
from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "financial_rules.toml"


class RulesError(ValueError):
    """The rules file is missing, unreadable or inconsistent."""


@dataclass(frozen=True)
class Bracket:
    up_to: Decimal | None  # None = no upper limit
    rate_pct: Decimal


@dataclass(frozen=True)
class Tier:
    max_ltv_pct: Decimal
    rate_pct: Decimal


@dataclass(frozen=True)
class Personal:
    first_time_buyer: bool
    legal_fees: Decimal
    title_insurance: Decimal
    inspection_costs: Decimal
    other_closing_costs: Decimal


@dataclass(frozen=True)
class Rules:
    version: str
    retrieved: str
    rules_hash: str                       # changes only when a rule changes (not personal settings or sources)
    personal: Personal
    stress_buffer_pct: Decimal
    stress_floor_pct: Decimal
    max_amortization_years: int
    max_amortization_years_ftb: int
    default_compounding: dict[str, str]
    max_insurable_price: Decimal
    uninsured_down_payment_pct: Decimal
    max_ltv_pct: Decimal
    extended_amortization_over_years: int
    extended_amortization_surcharge_pct: Decimal
    non_traditional_min_ltv_pct: Decimal
    non_traditional_rate_pct: Decimal
    pst_on_premium_pct: Decimal
    premium_tiers: tuple[Tier, ...]
    down_payment_minimum: tuple[Bracket, ...]   # up_to / rate_pct (= pct of the band)
    ontario_brackets: tuple[Bracket, ...]
    ontario_rebate_max: Decimal
    toronto_brackets: tuple[Bracket, ...]
    toronto_rebate_max: Decimal
    toronto_admin_fee: Decimal
    toronto_hst_pct: Decimal
    toronto_effective_from: str
    sources: tuple[dict[str, str], ...]


# --------------------------------------------------------------------------- parsing helpers
def _dec(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise RulesError(f"{where}: expected a number, got {value!r}.")
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise RulesError(f"{where}: {value!r} is not a number.") from exc


def _pct(value: Any, where: str) -> Decimal:
    d = _dec(value, where)
    if not (Decimal(0) <= d <= Decimal(100)):
        raise RulesError(f"{where}: {d} is not between 0 and 100.")
    return d


def _money(value: Any, where: str) -> Decimal:
    d = _dec(value, where)
    if d < 0:
        raise RulesError(f"{where}: {d} cannot be negative.")
    return d


def _int(value: Any, where: str, low: int = 1, high: int = 100) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (low <= value <= high):
        raise RulesError(f"{where}: expected a whole number between {low} and {high}, got {value!r}.")
    return value


def _section(data: dict, *path: str) -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise RulesError(f"Missing [{'.'.join(path)}] in the rules file.")
        node = node[key]
    return node


def _brackets(rows: Any, where: str, *, rate_key: str, last_open: bool) -> tuple[Bracket, ...]:
    if not isinstance(rows, list) or not rows:
        raise RulesError(f"{where}: at least one entry is required.")
    out: list[Bracket] = []
    previous = Decimal(0)
    for i, row in enumerate(rows):
        label = f"{where} #{i + 1}"
        rate = _pct(row.get(rate_key), f"{label} {rate_key}")
        last = i == len(rows) - 1
        if "up_to" in row:
            up_to = _money(row["up_to"], f"{label} up_to")
            if up_to <= previous:
                raise RulesError(f"{label}: up_to must be larger than the previous entry ({previous}).")
            previous = up_to
        else:
            up_to = None
        if last and last_open and up_to is not None:
            raise RulesError(f"{label}: the last bracket must have no up_to (it covers everything above).")
        if not last and up_to is None:
            raise RulesError(f"{label}: only the last bracket may omit up_to.")
        if last and not last_open and up_to is None:
            raise RulesError(f"{label}: this list needs an up_to on every entry.")
        out.append(Bracket(up_to, rate))
    return tuple(out)


def _tiers(rows: Any, max_ltv: Decimal) -> tuple[Tier, ...]:
    if not isinstance(rows, list) or not rows:
        raise RulesError("[[insurance.premium_tiers]]: at least one tier is required.")
    out, previous = [], Decimal(0)
    for i, row in enumerate(rows):
        ltv = _pct(row.get("max_ltv_pct"), f"premium tier #{i + 1} max_ltv_pct")
        if ltv <= previous:
            raise RulesError(f"premium tier #{i + 1}: max_ltv_pct must increase ({ltv} <= {previous}).")
        previous = ltv
        out.append(Tier(ltv, _pct(row.get("rate_pct"), f"premium tier #{i + 1} rate_pct")))
    if out[-1].max_ltv_pct != max_ltv:
        raise RulesError(
            f"The last premium tier ends at {out[-1].max_ltv_pct}% but max_ltv_pct is {max_ltv}%. They must match."
        )
    return tuple(out)


def _hash_of_rules(data: dict) -> str:
    """Hash of the rule sections only, so editing personal settings, sources or comments never
    marks saved results as out of date."""
    rules_only = {k: v for k, v in data.items() if k not in ("personal", "sources", "meta")}
    canonical = json.dumps(rules_only, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def parse_rules(text: str) -> Rules:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RulesError(f"The rules file is not valid TOML: {exc}") from exc

    meta = _section(data, "meta")
    pers = _section(data, "personal")
    if not isinstance(pers.get("first_time_buyer"), bool):
        raise RulesError("[personal] first_time_buyer must be true or false.")
    personal = Personal(
        first_time_buyer=pers["first_time_buyer"],
        legal_fees=_money(pers.get("legal_fees", 0), "[personal] legal_fees"),
        title_insurance=_money(pers.get("title_insurance", 0), "[personal] title_insurance"),
        inspection_costs=_money(pers.get("inspection_costs", 0), "[personal] inspection_costs"),
        other_closing_costs=_money(pers.get("other_closing_costs", 0), "[personal] other_closing_costs"),
    )

    stress = _section(data, "stress_test")
    mort = _section(data, "mortgage")
    ins = _section(data, "insurance")
    ont = _section(data, "land_transfer", "ontario")
    tor = _section(data, "land_transfer", "toronto")

    compounding = mort.get("default_compounding")
    if not isinstance(compounding, dict) or set(compounding) != {"Fixed", "Variable"}:
        raise RulesError("[mortgage] default_compounding needs exactly Fixed and Variable.")
    for kind, value in compounding.items():
        if value not in ("semi_annual", "monthly"):
            raise RulesError(f"[mortgage] default_compounding {kind}: use 'semi_annual' or 'monthly'.")

    max_ltv = _pct(ins.get("max_ltv_pct"), "[insurance] max_ltv_pct")
    max_price = _money(ins.get("max_insurable_price"), "[insurance] max_insurable_price")
    down_min = _brackets(data.get("down_payment_minimum"), "[[down_payment_minimum]]", rate_key="pct", last_open=False)
    if down_min[-1].up_to != max_price:
        raise RulesError("The last [[down_payment_minimum]] up_to must equal [insurance] max_insurable_price.")

    max_amort = _int(mort.get("max_amortization_years"), "[mortgage] max_amortization_years", 1, 40)
    max_amort_ftb = _int(mort.get("max_amortization_years_first_time_buyer"),
                         "[mortgage] max_amortization_years_first_time_buyer", 1, 40)
    if max_amort_ftb < max_amort:
        raise RulesError("First-time-buyer maximum amortization cannot be shorter than the standard maximum.")

    sources = data.get("sources", [])
    if not isinstance(sources, list):
        raise RulesError("[[sources]] must be a list of tables.")

    return Rules(
        version=str(meta.get("version", "")) or "unversioned",
        retrieved=str(meta.get("retrieved", "")),
        rules_hash=_hash_of_rules(data),
        personal=personal,
        stress_buffer_pct=_pct(stress.get("buffer_pct"), "[stress_test] buffer_pct"),
        stress_floor_pct=_pct(stress.get("floor_pct"), "[stress_test] floor_pct"),
        max_amortization_years=max_amort,
        max_amortization_years_ftb=max_amort_ftb,
        default_compounding=dict(compounding),
        max_insurable_price=max_price,
        uninsured_down_payment_pct=_pct(ins.get("uninsured_down_payment_pct"), "[insurance] uninsured_down_payment_pct"),
        max_ltv_pct=max_ltv,
        extended_amortization_over_years=_int(
            ins.get("extended_amortization_over_years"), "[insurance] extended_amortization_over_years", 1, 40),
        extended_amortization_surcharge_pct=_pct(
            ins.get("extended_amortization_surcharge_pct"), "[insurance] extended_amortization_surcharge_pct"),
        non_traditional_min_ltv_pct=_pct(ins.get("non_traditional_min_ltv_pct"), "[insurance] non_traditional_min_ltv_pct"),
        non_traditional_rate_pct=_pct(ins.get("non_traditional_rate_pct"), "[insurance] non_traditional_rate_pct"),
        pst_on_premium_pct=_pct(ins.get("pst_on_premium_pct"), "[insurance] pst_on_premium_pct"),
        premium_tiers=_tiers(ins.get("premium_tiers"), max_ltv),
        down_payment_minimum=down_min,
        ontario_brackets=_brackets(ont.get("brackets"), "[[land_transfer.ontario.brackets]]",
                                   rate_key="rate_pct", last_open=True),
        ontario_rebate_max=_money(ont.get("first_time_buyer_rebate_max"), "[land_transfer.ontario] rebate max"),
        toronto_brackets=_brackets(tor.get("brackets"), "[[land_transfer.toronto.brackets]]",
                                   rate_key="rate_pct", last_open=True),
        toronto_rebate_max=_money(tor.get("first_time_buyer_rebate_max"), "[land_transfer.toronto] rebate max"),
        toronto_admin_fee=_money(tor.get("administration_fee"), "[land_transfer.toronto] administration_fee"),
        toronto_hst_pct=_pct(tor.get("hst_pct"), "[land_transfer.toronto] hst_pct"),
        toronto_effective_from=str(tor.get("effective_from", "")),
        sources=tuple({str(k): str(v) for k, v in s.items()} for s in sources),
    )


@lru_cache(maxsize=8)
def _load_cached(path: str, mtime_ns: int) -> Rules:  # mtime in the key: edits are picked up without a restart
    return parse_rules(Path(path).read_text(encoding="utf-8"))


def load_rules(path: Path | str | None = None) -> Rules:
    target = Path(path) if path else DEFAULT_PATH
    try:
        mtime = target.stat().st_mtime_ns
    except FileNotFoundError as exc:
        raise RulesError(f"Rules file not found: {target}") from exc
    return _load_cached(str(target), mtime)


def sources_for(rules: Rules, ids: list[str]) -> list[dict[str, str]]:
    by_id = {s.get("id"): s for s in rules.sources}
    return [by_id[i] for i in ids if i in by_id]