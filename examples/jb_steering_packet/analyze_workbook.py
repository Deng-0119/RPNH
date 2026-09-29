"""Compute the public aggregate reference checks from prepared JB workbook TSV."""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import statistics


ARM = {"1": "U-MDT", "0": "R-MDT"}
TIMES = (180, 365, 730, 1825)


def _sheets(path: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("### SHEET: "):
            name = line.removeprefix("### SHEET: ").strip()
            current = result.setdefault(name, [])
        elif current is not None:
            current.append(line)
    return result


def _date(value: str) -> date:
    value = value.strip()
    if not value:
        raise ValueError("missing date")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()


def _records(path: Path) -> list[dict[str, str]]:
    lines = _sheets(path).get("UMDT DATA")
    if not lines:
        raise ValueError("prepared workbook lacks UMDT DATA")
    return list(csv.DictReader(lines, delimiter="\t"))


def _km(items: list[tuple[int, bool]]) -> dict[str, object]:
    survival = 1.0
    event_times = sorted({time for time, event in items if event})
    steps: dict[int, float] = {}
    for time in event_times:
        at_risk = sum(duration >= time for duration, _event in items)
        events = sum(
            duration == time and event for duration, event in items)
        survival *= 1.0 - events / at_risk
        steps[time] = survival
    estimates = {}
    for horizon in TIMES:
        prior = [time for time in event_times if time <= horizon]
        estimates[str(horizon)] = steps[prior[-1]] if prior else 1.0
    return {
        "events": sum(event for _time, event in items),
        "survival": estimates,
    }


def _log_rank(groups: dict[str, list[tuple[int, bool]]]) -> tuple[float, float]:
    left = groups["U-MDT"]
    right = groups["R-MDT"]
    times = sorted({
        time for items in groups.values() for time, event in items if event
    })
    observed = expected = variance = 0.0
    for time in times:
        n1 = sum(duration >= time for duration, _event in left)
        n0 = sum(duration >= time for duration, _event in right)
        d1 = sum(duration == time and event for duration, event in left)
        d0 = sum(duration == time and event for duration, event in right)
        total = n1 + n0
        events = d1 + d0
        if not events or total < 2:
            continue
        observed += d1
        expected += events * n1 / total
        variance += (
            n1 * n0 * events * (total - events)
            / (total * total * (total - 1)))
    chi_square = (observed - expected) ** 2 / variance
    return chi_square, math.erfc(math.sqrt(chi_square / 2.0))


def analyze(path: Path) -> dict[str, object]:
    rows = _records(path)
    if len(rows) != 613:
        raise ValueError(
            f"UMDT DATA must contain 613 participant rows, found {len(rows)}")
    by_arm: dict[str, list[dict[str, str]]] = {name: [] for name in ARM.values()}
    reaction: dict[str, list[tuple[int, bool]]] = {
        name: [] for name in ARM.values()}
    for row in rows:
        arm = ARM[row["TreatGroup"].strip()]
        by_arm[arm].append(row)
        start = _date(row["DT FIRST VISIT"])
        last = _date(row["DT LAST VISIT"])
        event_value = row["DT FIRST REACTION"].strip()
        event_date = _date(event_value) if event_value else None
        # A reaction outside the observed first-to-last-visit interval cannot
        # be an in-window failure. Keep the row and censor it at last visit.
        observed_event = (
            event_date is not None and start <= event_date <= last)
        end = event_date if observed_event else last
        reaction[arm].append(((end - start).days, observed_event))

    baseline: dict[str, object] = {}
    for arm, items in by_arm.items():
        ages = [float(row["AGE"]) for row in items]
        abi = [float(row["INICIAL aBI"]) for row in items]
        baseline[arm] = {
            "n": len(items),
            "age_mean": statistics.mean(ages),
            "age_sd": statistics.stdev(ages),
            "male_n": sum(row["SEX"].strip() == "M" for row in items),
            "abi_mean": statistics.mean(abi),
            "abi_sd": statistics.stdev(abi),
            "abi_ge_4_n": sum(value >= 4 for value in abi),
        }
    chi_square, p_value = _log_rank(reaction)
    evaluable = 278
    completed = 439
    total = len(rows)
    per_arm = math.ceil(evaluable / (completed / total))
    return {
        "sample_size": {
            "historical_evaluable_per_arm": evaluable,
            "observed_five_year_completion": f"{completed}/{total}",
            "recommended_randomized_per_arm": per_arm,
            "recommended_randomized_total": per_arm * 2,
        },
        "baseline": baseline,
        "time_to_first_reaction": {
            **{arm: _km(items) for arm, items in reaction.items()},
            "log_rank_chi_square": chi_square,
            "log_rank_p_value": p_value,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    value = analyze(args.workbook)
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
