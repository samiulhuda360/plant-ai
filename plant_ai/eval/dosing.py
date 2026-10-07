"""Dosing evaluation: fixed-rate baseline vs adaptive control on the same simulated influent.

Each strategy runs on identical seeds (the same dye-house batches, dumps and influent noise), three days per run,
with no instrument faults. Compliance is measured on the *true* discharge values of the model, so a strategy cannot
look good through measurement noise. Seeds 1-3 were used to set the controller gains; seeds 101-105 are held out.
"""

from __future__ import annotations

from ..sim.process import PlantSimulator

TUNE_SEEDS = (1, 2, 3)
HELDOUT_SEEDS = (101, 102, 103, 104, 105)
DAYS = 3

STRATEGIES = {
    "fixed": {
        "strategy": "fixed",
        "ph_sp": 7.3,
        "label": "Fixed-rate baseline (on/off acid 7.0-7.6, fixed PAC, blower 100 %)",
    },
    "adaptive": {
        "strategy": "adaptive",
        "ph_sp": 7.3,
        "label": "Adaptive (soft sensor + feed-forward + PI trims), pH 7.3",
    },
    "adaptive_ph7.6": {"strategy": "adaptive", "ph_sp": 7.6, "label": "Adaptive with the pH setpoint moved to 7.6"},
}


def run_strategy(name: str, seed: int, days: int = DAYS) -> dict:
    cfg = STRATEGIES[name]
    sim = PlantSimulator(seed=seed, strategy=cfg["strategy"])
    sim.ctrl.ph_sp = cfg["ph_sp"]
    minutes = days * 1440
    for _ in range(minutes):
        sim.step()
    return {
        "acid_l": float(sim.acid_l),
        "coag_l": float(sim.coag_l),
        "blower_kwh": float(sim.blower_kwh),
        "plant_kwh": float(sim.plant_kwh),
        "minutes": minutes,
        "out_minutes": dict(sim.out_minutes),
    }


def evaluate(seeds: tuple[int, ...], days: int = DAYS) -> dict:
    out: dict = {"seeds": list(seeds), "days_per_run": days, "strategies": {}}
    for name, cfg in STRATEGIES.items():
        runs = [run_strategy(name, s, days) for s in seeds]
        total_days = sum(r["minutes"] for r in runs) / 1440
        total_min = sum(r["minutes"] for r in runs)
        out["strategies"][name] = {
            "label": cfg["label"],
            "acid_l_per_day": round(sum(r["acid_l"] for r in runs) / total_days, 1),
            "coagulant_l_per_day": round(sum(r["coag_l"] for r in runs) / total_days, 1),
            "blower_kwh_per_day": round(sum(r["blower_kwh"] for r in runs) / total_days, 1),
            "plant_kwh_per_day": round(sum(r["plant_kwh"] for r in runs) / total_days, 1),
            "out_of_consent_pct": round(100 * sum(r["out_minutes"]["any"] for r in runs) / total_min, 2),
            "out_of_consent_min_by_param": {
                k: sum(r["out_minutes"][k] for r in runs) for k in ("AIT-501", "AIT-401", "AIT-502", "TT-501")
            },
        }
    base = out["strategies"]["fixed"]
    for _name, s in out["strategies"].items():
        s["acid_change_pct"] = round(100 * (s["acid_l_per_day"] / base["acid_l_per_day"] - 1), 1)
        s["coagulant_change_pct"] = round(100 * (s["coagulant_l_per_day"] / base["coagulant_l_per_day"] - 1), 1)
        s["blower_energy_change_pct"] = round(100 * (s["blower_kwh_per_day"] / base["blower_kwh_per_day"] - 1), 1)
    return out
