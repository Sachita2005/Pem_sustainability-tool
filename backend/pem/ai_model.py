"""The local recommendation model.

This is the stand-in for the company's internal AI server. It makes no
network calls and needs no model files: it is a small statistical model that
finds abnormal months ("red zones") in a monthly series, plus a rule library
that turns each finding into concrete actions. Everything it reports is
computed from the series it is given, and each number can be traced:

  expected[m]  what the month would normally be: the median of the same month
               in earlier years, scaled by how the latest 12 months compare
               with the 12 before them (so genuine growth is not flagged
               forever); when there is no earlier year, the median of the
               previous six months.
  band         how much a normal month strays from expected: 1.4826 x the
               median absolute deviation of (actual/expected - 1) over the
               series, never less than MIN_BAND.
  amber        actual above expected by more than AMBER_BANDS x band
  red          actual above expected by more than RED_BANDS x band

`AIProvider` is the seam: `LocalProvider` is used today; `InternalProvider`
posts the same findings to the company server once PEM_AI_URL is set, and
falls back to the local model whenever that call fails.
"""
from __future__ import annotations

import json
import statistics
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol

MIN_BAND = 0.05        # a month within 5% of expected is never abnormal
AMBER_BANDS = 2.0
RED_BANDS = 3.0
MIN_HISTORY = 6        # months needed before anything is judged
SUSTAINED_MONTHS = 3
SUSTAINED_RISE = 0.10  # each of the last 3 months 10% above the same month a year earlier
SUSPECT_RATIO = 5.0    # a month 5x its expected value ...
SUSPECT_PEAK_RATIO = 3.0  # ... and 3x the highest other month ever reported is a probable entry mistake


def _num(value: float | None) -> str:
    """A figure for a sentence: whole numbers when large, two decimals when small."""
    if value is None:
        return "—"
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}"


@dataclass
class Series:
    """One monthly series for one plant. `values[i]` belongs to `months[i]` ("YYYY-MM"); None = no data."""
    metric: str               # power | diesel | water | scrap | ...
    plant: str
    unit: str
    months: list[str]
    values: list[float | None]
    label: str = ""           # e.g. "Grid (EB) units"
    lower_is_better: bool = True


@dataclass
class Finding:
    id: str
    plant: str
    metric: str
    label: str
    unit: str
    kind: str                 # spike | sustained_rise
    severity: str             # red | amber
    months: list[str]         # the abnormal months
    observed: float           # total over those months
    expected: float           # what those months would normally total
    excess: float             # observed - expected
    excess_pct: float
    evidence: str
    chart: dict[str, Any] = field(default_factory=dict)


@dataclass
class Recommendation:
    finding_id: str
    title: str
    actions: list[str]
    rationale: str
    avoidable: float          # excess that goes away if the abnormal months return to the upper band
    unit: str
    source: str               # "local-model" | "internal-ai"
    related_initiatives: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def expected_series(months: list[str], values: list[float | None]) -> list[float | None]:
    """Expected value for each month, or None where there is not enough history to say."""
    by_month = {m: v for m, v in zip(months, values) if v is not None}
    out: list[float | None] = []
    for i, month in enumerate(months):
        year, mm = int(month[:4]), month[5:]
        same = [by_month[f"{y}-{mm}"] for y in range(year - 3, year) if f"{y}-{mm}" in by_month]
        if same:
            base = _median(same)
            recent = [v for v in values[max(0, i - 12):i] if v is not None]
            before = [v for v in values[max(0, i - 24):max(0, i - 12)] if v is not None]
            # Growth is only trusted when both windows are well populated.
            # Medians, so one wild month cannot pass itself off as growth.
            growth = statistics.median(recent) / statistics.median(before) if len(recent) >= 9 and len(before) >= 9 and statistics.median(before) > 0 else 1.0
            out.append(base * min(1.5, max(0.67, growth)) if base is not None else None)
            continue
        prior = [v for v in values[max(0, i - 6):i] if v is not None]
        out.append(_median(prior) if len(prior) >= 3 else None)
    return out


def normal_band(values: list[float | None], expected: list[float | None]) -> float:
    ratios = [v / e - 1 for v, e in zip(values, expected) if v is not None and e]
    if len(ratios) < MIN_HISTORY:
        return MIN_BAND * 2
    centre = statistics.median(ratios)
    mad = statistics.median([abs(r - centre) for r in ratios])
    return max(MIN_BAND, 1.4826 * mad)


def analyse(series: Series, last_n: int = 12) -> list[Finding]:
    """Findings in the most recent `last_n` reported months of a series."""
    points = [(m, v) for m, v in zip(series.months, series.values)]
    reported = [i for i, (_, v) in enumerate(points) if v is not None]
    if len(reported) < MIN_HISTORY or not series.lower_is_better:
        return []
    months = [m for m, _ in points]
    values = [v for _, v in points]
    expected = expected_series(months, values)
    band = normal_band(values, expected)
    window = set(reported[-last_n:])

    levels: list[str | None] = []
    for i, (v, e) in enumerate(zip(values, expected)):
        level = None
        if i in window and v is not None and e:
            over = v / e - 1
            if over > RED_BANDS * band:
                level = "red"
            elif over > AMBER_BANDS * band:
                level = "amber"
        levels.append(level)

    label = series.label or series.metric
    chart = {
        "months": months[-max(last_n, 18):],
        "actual": values[-max(last_n, 18):],
        "expected": [None if e is None else round(e, 2) for e in expected[-max(last_n, 18):]],
        "upper": [None if e is None else round(e * (1 + AMBER_BANDS * band), 2) for e in expected[-max(last_n, 18):]],
        "levels": levels[-max(last_n, 18):],
        "bandPct": round(band * 100, 1),
    }
    findings: list[Finding] = []

    # A month several times its expected value is far more likely a typing or
    # unit mistake (litres entered as kilolitres, an extra digit) than a real
    # operating event. Those are reported as entries to check, with no advice
    # and no claimed saving, and kept out of the operating findings.
    # "Unprecedented" as well as "many times expected": a small series (a DG that
    # barely runs) can be 5x its usual month without anything being mistyped.
    candidates = [i for i, lv in enumerate(levels) if lv and values[i] / expected[i] >= SUSPECT_RATIO]  # type: ignore[operator]
    usual_peak = max((v for j, v in enumerate(values) if v is not None and j not in candidates), default=0.0)
    suspect = [i for i in candidates if values[i] >= SUSPECT_PEAK_RATIO * usual_peak]  # type: ignore[operator]
    if suspect:
        for i in suspect:
            levels[i] = "check"
        chart["levels"] = levels[-max(last_n, 18):]
        observed = sum(values[i] for i in suspect)            # type: ignore[misc]
        normal = sum(expected[i] for i in suspect)            # type: ignore[misc]
        detail = "; ".join(f"{months[i]}: {_num(values[i])} {series.unit} against an expected {_num(expected[i])}" for i in suspect)
        findings.append(Finding(
            id=f"{series.plant}:{series.metric}:{label}:check".replace(" ", "_"),
            plant=series.plant, metric=series.metric, label=label, unit=series.unit,
            kind="suspect_entry", severity="check", months=[months[i] for i in suspect],
            observed=round(observed, 2), expected=round(normal, 2), excess=round(observed - normal, 2),
            excess_pct=round((observed / normal - 1) * 100, 1) if normal else 0.0,
            evidence=(f"{len(suspect)} month(s) are {SUSPECT_RATIO:.0f} or more times the expected value and at least "
                      f"{SUSPECT_PEAK_RATIO:.0f} times the highest other month on record, which usually "
                      f"means a typing or unit mistake in the source sheet rather than real consumption. {detail}."),
            chart=chart,
        ))

    flagged = [i for i, lv in enumerate(levels) if lv in ("red", "amber")]
    if flagged:
        severity = "red" if any(levels[i] == "red" for i in flagged) else "amber"
        observed = sum(values[i] for i in flagged)            # type: ignore[misc]
        normal = sum(expected[i] for i in flagged)            # type: ignore[misc]
        worst = max(flagged, key=lambda i: values[i] / expected[i])  # type: ignore[operator]
        findings.append(Finding(
            id=f"{series.plant}:{series.metric}:{label}:spike".replace(" ", "_"),
            plant=series.plant, metric=series.metric, label=label, unit=series.unit,
            kind="spike", severity=severity, months=[months[i] for i in flagged],
            observed=round(observed, 2), expected=round(normal, 2), excess=round(observed - normal, 2),
            excess_pct=round((observed / normal - 1) * 100, 1) if normal else 0.0,
            evidence=(f"{len(flagged)} of the last {len(window)} reported months are above the normal range "
                      f"(normal month-to-month variation is ±{band * 100:.0f}%). The largest is {months[worst]}: "
                      f"{_num(values[worst])} {series.unit} against an expected {_num(expected[worst])}."),
            chart=chart,
        ))

    # Sustained rise: the last three reported months each well above the same month a year earlier.
    tail = reported[-SUSTAINED_MONTHS:]
    by_month = dict(zip(months, values))
    pairs = []
    for i in tail:
        prev = by_month.get(f"{int(months[i][:4]) - 1}-{months[i][5:]}")
        if prev:
            pairs.append((values[i], prev))
    steady = len(pairs) == SUSTAINED_MONTHS and all(SUSTAINED_RISE < v / p - 1 < SUSPECT_RATIO - 1 for v, p in pairs)
    if steady and not flagged and not suspect:
        now, then = sum(v for v, _ in pairs), sum(p for _, p in pairs)
        findings.append(Finding(
            id=f"{series.plant}:{series.metric}:{label}:rise".replace(" ", "_"),
            plant=series.plant, metric=series.metric, label=label, unit=series.unit,
            kind="sustained_rise", severity="amber", months=[months[i] for i in tail],
            observed=round(now, 2), expected=round(then, 2), excess=round(now - then, 2),
            excess_pct=round((now / then - 1) * 100, 1),
            evidence=(f"Each of the last {SUSTAINED_MONTHS} reported months is more than {SUSTAINED_RISE * 100:.0f}% above "
                      f"the same month a year earlier ({_num(now)} {series.unit} against {_num(then)})."),
            chart=chart,
        ))
    return findings


def projected(finding: Finding) -> dict[str, Any]:
    """The same chart with the abnormal months brought back to the top of the normal range,
    and how much that removes. This is what "eliminating the red zone" means in numbers."""
    chart = finding.chart
    after, avoided = [], 0.0
    for v, upper, level in zip(chart.get("actual", []), chart.get("upper", []), chart.get("levels", [])):
        if level in ("red", "amber") and v is not None and upper is not None and v > upper:
            after.append(upper)
            avoided += v - upper
        else:
            after.append(v)
    return {"months": chart.get("months", []), "after": after, "avoidable": round(avoided, 2)}


# ---------------------------------------------------------------------------
# Rule library: finding -> actions
# ---------------------------------------------------------------------------

_SUMMER = {"03", "04", "05", "06"}

RULES: dict[str, dict[str, list[str]]] = {
    "power": {
        "summer": [
            "Check HVAC set-points and schedules for the flagged months: confirm chilled-water and room set-points were not lowered, and that AHUs and ODUs are switched off outside shift hours.",
            "Clean condenser coils and AHU filters before the hot months, and check refrigerant charge on the units serving the largest bays.",
            "Compare the month's HVAC run hours with production hours; cooling an empty bay is the usual cause of a summer peak.",
        ],
        "default": [
            "Compare the flagged months' sub-meter readings by machine family to find which family carried the increase.",
            "Run a compressed-air leak survey and check compressor load/unload hours for the flagged months.",
            "Check for equipment left running on weekends and holidays in those months (base load on non-working days).",
            "Review power factor and maximum demand on the electricity bill for the same months.",
        ],
    },
    "diesel": {
        "default": [
            "List the grid outages in the flagged months and the DG run hours against them; DG hours without a logged outage point to testing or manual running.",
            "Check DG specific fuel consumption (units generated per litre) for those months against the set's rated figure.",
            "Confirm diesel issued matches diesel consumed (stock reconciliation) for the flagged months.",
        ],
    },
    "water": {
        "summer": [
            "Check cooling-tower make-up and blow-down settings for the flagged months; make-up rises sharply when bleed is left open.",
            "Confirm treated STP/ETP water was used for gardening and flushing in those months rather than fresh water.",
        ],
        "default": [
            "Take night-flow readings on the main inlet meter to check for leaks: flow when the plant is idle is leakage.",
            "Compare the flagged months' flow-meter readings area by area to find which line carried the increase.",
            "Check that every flow meter on the affected lines is working; a failed meter hides where the water went.",
        ],
    },
    "scrap": {
        "default": [
            "Break the flagged months' scrap down by category and by generating area to see which stream rose.",
            "Check whether a one-off disposal (accumulated stock cleared in one month) explains the peak before treating it as a process problem.",
            "For process scrap, review rejections and set-up losses on the lines that generated it.",
        ],
    },
}


def actions_for(finding: Finding) -> list[str]:
    rules = RULES.get(finding.metric, {})
    in_summer = any(m[5:] in _SUMMER for m in finding.months)
    picked = list(rules.get("summer", [])) if in_summer else []
    picked += rules.get("default", [])
    return picked or ["Review the flagged months against operating records to find what changed."]


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class AIProvider(Protocol):
    name: str

    def recommend(self, findings: list[Finding]) -> list[Recommendation]: ...


class LocalProvider:
    name = "local-model"

    def recommend(self, findings: list[Finding]) -> list[Recommendation]:
        out = []
        for f in findings:
            if f.kind == "suspect_entry":
                out.append(Recommendation(
                    finding_id=f.id,
                    title=f"{f.plant}: check the {f.label.lower()} figure entered for {', '.join(f.months)}",
                    actions=[
                        "Open the source sheet and confirm the figure and its unit for the month(s) listed (for example litres entered in a kilolitre column, or an extra digit).",
                        "Compare it with the utility bill, meter reading or issue register for the same month.",
                        "If it is a mistake, correct it in the workbook; this page updates from the workbook. If it is real, record what happened that month.",
                    ],
                    rationale=f.evidence, avoidable=0.0, unit=f.unit, source=self.name))
                continue
            plan = projected(f)
            what = "above its normal range" if f.kind == "spike" else "rising year on year"
            out.append(Recommendation(
                finding_id=f.id,
                title=f"{f.plant}: {f.label} {what} ({f.excess_pct:+.1f}%)",
                actions=actions_for(f),
                rationale=f.evidence,
                avoidable=plan["avoidable"] if f.kind == "spike" else f.excess,
                unit=f.unit,
                source=self.name,
            ))
        return out


class InternalProvider:
    """The company's AI server.

    Assumed contract (change `_call` only, when the real one is known):
      POST {PEM_AI_URL}  body {"model": <PEM_AI_MODEL>, "findings": [Finding as JSON, ...]}
      header Authorization: Bearer <PEM_AI_API_KEY> when a key is set
      response {"recommendations": [{"finding_id", "title", "actions": [...], "rationale"}]}
    Any failure, or a finding the server does not answer, falls back to the local model.
    """
    name = "internal-ai"

    def __init__(self, url: str, api_key: str = "", model: str = "", timeout: float = 20.0,
                 post: Callable[[str, dict, dict, float], dict] | None = None) -> None:
        self.url, self.api_key, self.model, self.timeout = url, api_key, model, timeout
        self._post = post or self._http_post
        self.last_error = ""

    @staticmethod
    def _http_post(url: str, body: dict, headers: dict, timeout: float) -> dict:
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL comes from settings
            return json.loads(response.read().decode("utf-8"))

    def _call(self, findings: list[Finding]) -> dict[str, dict]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        reply = self._post(self.url, {"model": self.model, "findings": [asdict(f) for f in findings]}, headers, self.timeout)
        return {r["finding_id"]: r for r in reply.get("recommendations", []) if r.get("finding_id")}

    def recommend(self, findings: list[Finding]) -> list[Recommendation]:
        local = {r.finding_id: r for r in LocalProvider().recommend(findings)}
        self.last_error = ""
        try:
            remote = self._call(findings) if findings else {}
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
            self.last_error = str(exc)
            return list(local.values())
        out = []
        for f in findings:
            base, r = local[f.id], remote.get(f.id)
            if r and r.get("actions"):
                out.append(Recommendation(
                    finding_id=f.id, title=str(r.get("title") or base.title),
                    actions=[str(a) for a in r["actions"]], rationale=str(r.get("rationale") or base.rationale),
                    avoidable=base.avoidable, unit=base.unit, source=self.name))
            else:
                out.append(base)
        return out


def get_provider(provider: str, url: str = "", api_key: str = "", model: str = "") -> AIProvider:
    if provider == "internal" and url:
        return InternalProvider(url, api_key, model)
    return LocalProvider()
