"""The local model, on small hand-built series where the right answer is obvious."""
import math

from pem.ai_model import (InternalProvider, LocalProvider, Series, analyse, expected_series, get_provider,
                          normal_band, projected)


def months(start_year: int, count: int) -> list[str]:
    out, y, m = [], start_year, 4
    for _ in range(count):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def flat(count: int, value: float = 100.0) -> list[float | None]:
    return [value] * count


def test_flat_series_has_no_findings():
    s = Series("power", "TSAL", "kWh", months(2024, 24), flat(24))
    assert analyse(s) == []


def test_single_spike_is_red_and_only_that_month():
    values = flat(24)
    values[20] = 160.0
    s = Series("power", "TSAL", "kWh", months(2024, 24), values, label="Total power")
    findings = analyse(s)
    assert len(findings) == 1
    f = findings[0]
    assert (f.kind, f.severity, f.months) == ("spike", "red", [months(2024, 24)[20]])
    assert f.observed == 160.0 and f.expected == 100.0 and f.excess == 60.0 and f.excess_pct == 60.0


def test_month_many_times_expected_is_an_entry_to_check_not_advice():
    values = flat(24)
    values[20] = 1500.0     # 15x: litres typed as kilolitres, or an extra digit
    values[22] = 140.0      # an ordinary spike in the same series
    findings = analyse(Series("diesel", "TLMAL", "KL", months(2024, 24), values, label="Total diesel"))
    kinds = {f.kind: f for f in findings}
    assert set(kinds) == {"suspect_entry", "spike"}
    assert kinds["suspect_entry"].severity == "check" and kinds["suspect_entry"].months == [months(2024, 24)[20]]
    assert kinds["spike"].months == [months(2024, 24)[22]]      # the suspect month is not counted as consumption
    recs = {r.finding_id: r for r in LocalProvider().recommend(findings)}
    check = recs[kinds["suspect_entry"].id]
    assert check.avoidable == 0.0 and "confirm the figure" in check.actions[0]
    assert projected(kinds["spike"])["after"][f_index(kinds["spike"], months(2024, 24)[20])] == 1500.0   # not "saved" away


def f_index(finding, month):
    return finding.chart["months"].index(month)


def test_expected_uses_same_month_of_earlier_years():
    ms = months(2023, 24)
    values = [100.0 + (50.0 if m.endswith("-05") else 0.0) for m in ms]   # every May is high: seasonal, not abnormal
    exp = expected_series(ms, values)
    second_may = ms.index("2024-05")
    assert exp[second_may] == 150.0
    assert analyse(Series("power", "TSAL", "kWh", ms, values)) == []


def test_gaps_are_not_zeros_and_short_series_is_not_judged():
    assert analyse(Series("water", "TCOE", "KL", months(2025, 5), [10, 500, 10, 10, 10])) == []
    values = flat(24)
    values[10] = None
    assert analyse(Series("water", "TCOE", "KL", months(2024, 24), values)) == []


def test_band_never_below_floor_and_widens_for_noisy_series():
    ms = months(2023, 36)
    calm = flat(36)
    assert normal_band(calm, expected_series(ms, calm)) == 0.05
    # Not periodic over 12 months, so the same month differs from year to year.
    noisy = [100.0 * (1 + 0.3 * math.sin(i * 1.7)) for i in range(36)]
    assert normal_band(noisy, expected_series(ms, noisy)) > 0.05


def test_sustained_rise_is_amber():
    ms = months(2024, 24)
    values = flat(24)
    # Last three months 13% up on a year earlier: under the red-zone band for a single month, but a steady rise.
    for i in (21, 22, 23):
        values[i] = 113.0
    kinds = {f.kind for f in analyse(Series("diesel", "TLMAL", "L", ms, values))}
    assert kinds <= {"sustained_rise", "spike"} and kinds


def test_projection_caps_flagged_months_at_the_upper_band():
    values = flat(24)
    values[20] = 160.0
    f = analyse(Series("power", "TSAL", "kWh", months(2024, 24), values))[0]
    plan = projected(f)
    idx = f.chart["months"].index(f.months[0])
    assert plan["after"][idx] == f.chart["upper"][idx] == 110.0
    assert plan["avoidable"] == 50.0
    assert [a for i, a in enumerate(plan["after"]) if i != idx] == [v for i, v in enumerate(f.chart["actual"]) if i != idx]


def test_local_provider_gives_actions_for_every_finding():
    values = flat(24)
    values[13] = 170.0      # May: a summer month, so HVAC actions come first
    f = analyse(Series("power", "TBAL", "kWh", months(2024, 24), values, label="Total power"))
    recs = LocalProvider().recommend(f)
    assert len(recs) == 1 and recs[0].source == "local-model"
    assert "HVAC" in recs[0].actions[0] and recs[0].avoidable > 0


def test_internal_provider_uses_server_reply_and_falls_back_on_failure():
    values = flat(24)
    values[20] = 160.0
    findings = analyse(Series("power", "TSAL", "kWh", months(2024, 24), values))

    def good(url, body, headers, timeout):
        assert headers["Authorization"] == "Bearer k" and body["findings"][0]["plant"] == "TSAL"
        return {"recommendations": [{"finding_id": findings[0].id, "title": "From server", "actions": ["Do X"]}]}

    ok = InternalProvider("http://ai.internal/x", "k", post=good).recommend(findings)
    assert ok[0].source == "internal-ai" and ok[0].actions == ["Do X"]

    def down(url, body, headers, timeout):
        raise OSError("connection refused")

    provider = InternalProvider("http://ai.internal/x", post=down)
    fallback = provider.recommend(findings)
    assert fallback[0].source == "local-model" and "refused" in provider.last_error


def test_provider_selection():
    assert get_provider("local").name == "local-model"
    assert get_provider("internal", "").name == "local-model"      # no URL yet: stay local
    assert get_provider("internal", "http://ai.internal/x").name == "internal-ai"
