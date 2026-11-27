"""Recommendations for the Initiatives page's "Upcoming" tab.

Feeds the real monthly series (power, diesel, water) of every plant to the
recommendation model (`pem.ai_model`), which finds abnormal months and turns
each finding into actions. The model used is chosen by PEM_AI_PROVIDER:
"local" (built in, no network) or "internal" (the company AI server, once its
address is set); see `pem.ai_model` for both.

Where the initiatives data shows an initiative already implemented at another
plant but still open at the plant with the finding, it is attached to the
recommendation: that is a proven action with the savings the other plant
reported, rather than generic advice.
"""
from __future__ import annotations

import importlib
import threading
import time
from dataclasses import asdict
from typing import Any, Callable

from fastapi import APIRouter

from ..ai_model import Finding, Series, analyse, get_provider, projected
from ..config import settings
from ..plants import PLANT_IDS, plant_name

router = APIRouter(prefix="/api/ai", tags=["ai"])

_CACHE_SECONDS = 600
_cache: dict[str, Any] = {"at": 0.0, "value": None}
_lock = threading.Lock()

METRICS = {
    "power": "Electricity",
    "diesel": "Diesel",
    "water": "Water",
}


def _module(name: str):
    """A sibling data module, or None if it is missing or broken (reported in notes)."""
    try:
        return importlib.import_module(f"pem.domains.{name}")
    except Exception:  # noqa: BLE001 - one missing source must not stop the others
        return None


def _positive(value: Any) -> float | None:
    """A reported value; blanks and zeros (months not yet entered) become None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _rows_to_series(metric: str, plant: str, unit: str, label: str, rows: list[dict], field: str) -> Series | None:
    months = [r["month"] for r in rows if r.get("month")]
    values = [_positive(r.get(field)) for r in rows if r.get("month")]
    if sum(v is not None for v in values) < 6:
        return None
    return Series(metric=metric, plant=plant, unit=unit, months=months, values=values, label=label)


def collect_series() -> tuple[list[Series], list[str]]:
    """Every monthly series the model looks at, plus notes about sources that were unavailable."""
    series: list[Series] = []
    notes: list[str] = []

    energy = _module("energy")
    if energy is None:
        notes.append("Energy data is unavailable, so electricity and diesel were not analysed.")
    else:
        power = energy.power_monthly()
        for block in power.get("plants", []):
            if block.get("plant") in PLANT_IDS:
                s = _rows_to_series("power", block["plant"], power.get("unit", "kWh"), "Total electricity", block.get("months", []), "total")
                if s:
                    series.append(s)
        diesel = energy.diesel_monthly()
        for block in diesel.get("plants", []):
            if block.get("plant") in PLANT_IDS:
                # Months the energy module itself flags as probable unit mistakes are left out:
                # the model should find operating problems, not typing errors.
                rows = [r for r in block.get("months", []) if not r.get("outlier")]
                s = _rows_to_series("diesel", block["plant"], diesel.get("unit", "KL"), "Total diesel", rows, "total")
                if s:
                    series.append(s)

    water = _module("water")
    monthly = getattr(water, "entity_monthly", None) if water else None
    if monthly is None:
        notes.append("Water data is unavailable, so fresh-water use was not analysed.")
    else:
        for plant in PLANT_IDS:
            try:
                rows = [{"month": r.get("month"), "value": (r.get("fresh") or {}).get("total")} for r in monthly(plant)]
            except Exception:  # noqa: BLE001 - one plant's sheet must not stop the rest
                notes.append(f"Water data for {plant_name(plant)} could not be read.")
                continue
            s = _rows_to_series("water", plant, "KL", "Fresh water", rows, "value")
            if s:
                series.append(s)
    return series, notes


def _related_initiatives(plant: str) -> list[dict[str, Any]]:
    initiatives = _module("initiatives")
    finder: Callable | None = getattr(initiatives, "horizontal_deployment_candidates", None) if initiatives else None
    if finder is None:
        return []
    try:
        found = finder(plant)
    except Exception:  # noqa: BLE001
        return []
    items = found.get("candidates", found) if isinstance(found, dict) else found
    return list(items)[:5] if isinstance(items, list) else []


def build() -> dict[str, Any]:
    series, notes = collect_series()
    findings: list[Finding] = []
    for s in series:
        findings.extend(analyse(s))

    provider = get_provider(settings.ai_provider, settings.ai_url, settings.ai_api_key, settings.ai_model)
    recommendations = {r.finding_id: r for r in provider.recommend(findings)}
    if getattr(provider, "last_error", ""):
        notes.append(f"The internal AI server could not be reached ({provider.last_error}); the built-in model was used instead.")

    severity_rank = {"red": 0, "amber": 1, "check": 2}
    items = []
    for f in sorted(findings, key=lambda x: (severity_rank.get(x.severity, 9), -abs(x.excess_pct))):
        rec = recommendations.get(f.id)
        related = _related_initiatives(f.plant) if f.metric == "power" else []
        items.append({
            **asdict(f),
            "plantName": plant_name(f.plant),
            "metricLabel": METRICS.get(f.metric, f.metric),
            "projection": projected(f),
            "recommendation": None if rec is None else {**asdict(rec), "related_initiatives": related},
        })

    return {
        "provider": {
            "name": provider.name,
            "configured": settings.ai_provider,
            "internalUrlSet": bool(settings.ai_url),
            "description": ("Built-in statistical model: no data leaves this server."
                            if provider.name == "local-model" else "Company internal AI server, with the built-in model as fallback."),
        },
        "method": {
            "expected": "Median of the same month in up to three earlier years, scaled by the change in the last 12 months against the 12 before; without an earlier year, the median of the previous six months.",
            "band": "Normal variation = 1.4826 x median absolute deviation of actual/expected over the series, never below 5%.",
            "amber": "Above expected by more than 2 x the normal variation.",
            "red": "Above expected by more than 3 x the normal variation.",
            "check": "Five or more times the expected value and three or more times the highest other month on record: treated as a probable typing or unit mistake to verify, not as consumption to reduce.",
            "window": "Only the last 12 reported months of each series are judged.",
            "projection": "Flagged months brought back to the top of the normal range; the difference is the avoidable amount.",
        },
        "seriesAnalysed": len(series),
        "counts": {
            "red": sum(1 for f in findings if f.severity == "red"),
            "amber": sum(1 for f in findings if f.severity == "amber"),
            "check": sum(1 for f in findings if f.severity == "check"),
            "byPlant": {p: sum(1 for f in findings if f.plant == p) for p in PLANT_IDS},
            "byMetric": {m: sum(1 for f in findings if f.metric == m) for m in METRICS},
        },
        "findings": items,
        "notes": notes,
    }


def recommendations(fresh: bool = False) -> dict[str, Any]:
    with _lock:
        if not fresh and _cache["value"] is not None and time.time() - _cache["at"] < _CACHE_SECONDS:
            return _cache["value"]
        value = build()
        _cache.update(at=time.time(), value=value)
        return value


@router.get("/recommendations")
def recommendations_route(plant: str | None = None, metric: str | None = None, fresh: bool = False) -> dict[str, Any]:
    data = recommendations(fresh)
    if not plant and not metric:
        return data
    kept = [f for f in data["findings"] if (not plant or f["plant"] == plant) and (not metric or f["metric"] == metric)]
    return {**data, "findings": kept}


@router.get("/status")
def status_route() -> dict[str, Any]:
    return {
        "configured": settings.ai_provider,
        "internalUrlSet": bool(settings.ai_url),
        "active": get_provider(settings.ai_provider, settings.ai_url, settings.ai_api_key, settings.ai_model).name,
        "howToConnect": "Set PEM_AI_PROVIDER=internal and PEM_AI_URL (and PEM_AI_API_KEY / PEM_AI_MODEL if needed) in .env, then restart.",
    }
