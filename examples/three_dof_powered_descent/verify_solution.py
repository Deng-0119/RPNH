"""Independently verify a 3-DOF example trajectory using only the stdlib."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


R0 = (2400.0, 450.0, -330.0)
V0 = (-10.0, -40.0, 10.0)
M0 = 1905.0
G = (-3.71, 0.0, 0.0)
ALPHA = 0.000453
TMIN = 5580.0
TMAX = 18600.0
MDRY = 1505.0
VMAX = 150.0
GLIDE = 14.300666256711942


def _norm(values) -> float:
    return math.sqrt(sum(float(value) ** 2 for value in values))


def verify(document: object) -> dict[str, object]:
    failures: list[str] = []
    if not isinstance(document, dict) or not isinstance(
            document.get("samples"), list):
        return {"accepted": False, "failures": ["samples array missing"]}
    try:
        samples = [[float(value) for value in row]
                   for row in document["samples"]]
    except (TypeError, ValueError):
        return {"accepted": False, "failures": ["samples must be numeric rows"]}
    if len(samples) < 2 or any(len(row) != 11 for row in samples):
        return {"accepted": False, "failures": ["invalid sample shape"]}
    if any(not math.isfinite(value) for row in samples for value in row):
        return {
            "accepted": False,
            "failures": ["samples must contain only finite numbers"],
        }
    first = samples[0]
    if max(abs(first[1 + i] - R0[i]) for i in range(3)) > 1e-9:
        failures.append("initial position mismatch")
    if max(abs(first[4 + i] - V0[i]) for i in range(3)) > 1e-9:
        failures.append("initial velocity mismatch")
    if abs(first[7] - M0) > 1e-9:
        failures.append("initial mass mismatch")

    maximum_residual = 0.0
    impulse = 0.0
    for index, (current, successor) in enumerate(
            zip(samples[:-1], samples[1:])):
        dt = current[0]
        if not 0 < dt <= 0.01 + 1e-14:
            failures.append(f"invalid propagation step at row {index}")
            continue
        r, v, mass, thrust = current[1:4], current[4:7], current[7], current[8:11]
        q = _norm(thrust)
        predicted = [
            *(r[i] + dt * v[i] for i in range(3)),
            *(v[i] + dt * (G[i] + thrust[i] / max(mass, 1.0))
              for i in range(3)),
            mass - dt * ALPHA * q,
        ]
        maximum_residual = max(
            maximum_residual,
            max(abs(predicted[i] - successor[i + 1]) for i in range(7)))
        impulse += dt * q

    propagation = samples[:-1]
    thrust_norms = [_norm(row[8:11]) for row in propagation]
    speeds = [_norm(row[4:7]) for row in propagation]
    glide_values = [
        math.hypot(row[2], row[3]) - GLIDE * row[1]
        for row in propagation
    ]
    final = samples[-1]
    if abs(final[0]) > 1e-14:
        failures.append("final row dt must be zero")
    terminal_position = _norm(final[1:4])
    terminal_velocity = _norm(final[4:7])
    metrics = {
        "maximum_euler_residual": maximum_residual,
        "minimum_thrust_N": min(thrust_norms),
        "maximum_thrust_N": max(thrust_norms),
        "minimum_altitude_m": min(row[1] for row in propagation),
        "maximum_glide_residual_m": max(glide_values),
        "maximum_speed_m_per_s": max(speeds),
        "minimum_mass_kg": min(row[7] for row in propagation),
        "terminal_position_error_m": terminal_position,
        "terminal_velocity_error_m_per_s": terminal_velocity,
        "fuel_accounting_error_kg": abs((M0 - final[7]) - ALPHA * impulse),
    }
    checks = (
        (maximum_residual <= 2e-9, "Euler recurrence"),
        (min(thrust_norms) >= TMIN - 1e-6, "minimum thrust"),
        (max(thrust_norms) <= TMAX + 1e-6, "maximum thrust"),
        (metrics["minimum_altitude_m"] >= -1e-6, "above ground"),
        (metrics["maximum_glide_residual_m"] <= 1e-6, "glide slope"),
        (metrics["maximum_speed_m_per_s"] <= VMAX + 1e-6, "speed"),
        (metrics["minimum_mass_kg"] >= MDRY - 1e-6, "dry mass"),
        (terminal_position <= 0.5, "terminal position"),
        (terminal_velocity <= 0.05, "terminal velocity"),
        (metrics["fuel_accounting_error_kg"] <= 1e-6, "fuel accounting"),
    )
    failures.extend(name for passed, name in checks if not passed)
    return {"accepted": not failures, "metrics": metrics, "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("solution", type=Path)
    args = parser.parse_args()
    result = verify(json.loads(args.solution.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
