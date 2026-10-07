"""Multivariate statistical process monitoring with PCA (numpy only).

The model is fitted on fault-free operation. Each new sample is scored with Hotelling's T-squared (unusual
movement *within* the normal correlation structure, e.g. an extreme shock load) and the squared prediction error
SPE/Q (a *broken* correlation, e.g. blower speed high while blower power reads zero). Per-variable SPE
contributions say which tags moved the statistic, which the assistant uses as evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

PCA_TAGS = [
    "FIT-101",
    "AIT-101",
    "TT-101",
    "AIT-102",
    "AIT-103",
    "LIT-101",
    "FV-101",
    "ZT-101",
    "FIT-102",
    "AIT-201",
    "AIT-201B",
    "P-201_SP",
    "P-202_SP",
    "FIT-202",
    "AIT-301",
    "SC-301",
    "JT-301",
    "AIT-401",
    "AIT-501",
    "AIT-502",
    "TT-501",
]


@dataclass
class PCAModel:
    tags: list[str]
    mean: np.ndarray
    std: np.ndarray
    loadings: np.ndarray  # (n_vars, k)
    variances: np.ndarray  # (k,)
    t2_limit: float
    spe_limit: float

    @classmethod
    def fit(cls, x: np.ndarray, tags: list[str], explained: float = 0.9, quantile: float = 0.999) -> PCAModel:
        std = x.std(axis=0)
        keep = std > 1e-6  # constant columns (e.g. a blower at fixed speed) carry no information
        x, std = x[:, keep], std[keep]
        tags = [t for t, k in zip(tags, keep, strict=True) if k]
        mean = x.mean(axis=0)
        z = (x - mean) / std
        _, s, vt = np.linalg.svd(z, full_matrices=False)
        var = s**2 / (len(z) - 1)
        k = int(np.searchsorted(np.cumsum(var) / var.sum(), explained) + 1)
        model = cls(tags, mean, std, vt[:k].T, var[:k], 0.0, 0.0)
        t2, spe = model.statistics(x)
        model.t2_limit = float(np.quantile(t2, quantile))
        model.spe_limit = float(np.quantile(spe, quantile))
        return model

    def statistics(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        z = (np.atleast_2d(x) - self.mean) / self.std
        scores = z @ self.loadings
        t2 = (scores**2 / self.variances).sum(axis=1)
        resid = z - scores @ self.loadings.T
        return t2, (resid**2).sum(axis=1)

    def contributions(self, x: np.ndarray) -> dict[str, float]:
        """Share of the SPE (or of T-squared when SPE is normal) explained by each tag."""
        z = (np.asarray(x) - self.mean) / self.std
        scores = z @ self.loadings
        resid = z - scores @ self.loadings.T
        c = resid**2
        if c.sum() < self.spe_limit:
            c = z**2
        c = c / max(c.sum(), 1e-12)
        return {t: float(v) for t, v in zip(self.tags, c, strict=True)}

    def to_dict(self) -> dict:
        return {
            "tags": self.tags,
            "mean": self.mean.tolist(),
            "std": self.std.tolist(),
            "loadings": self.loadings.tolist(),
            "variances": self.variances.tolist(),
            "t2_limit": self.t2_limit,
            "spe_limit": self.spe_limit,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PCAModel:
        return cls(
            d["tags"],
            np.array(d["mean"]),
            np.array(d["std"]),
            np.array(d["loadings"]),
            np.array(d["variances"]),
            d["t2_limit"],
            d["spe_limit"],
        )


class EWMA:
    """Exponential smoothing applied before scoring, so single noisy samples do not trip the statistics."""

    def __init__(self, alpha: float = 0.2) -> None:
        self.alpha = alpha
        self.state: np.ndarray | None = None

    def __call__(self, x: np.ndarray) -> np.ndarray:
        self.state = x.copy() if self.state is None else self.state + self.alpha * (x - self.state)
        return self.state


TRAIN_SEEDS = (900, 901, 902, 903, 904, 905)  # fault-free runs used only for fitting; never scored


def train_default(strategy: str = "adaptive", minutes: int = 3 * 1440) -> PCAModel:
    from ..sim.process import PlantSimulator, run
    from ..tags import TAG_NAMES

    idx = [TAG_NAMES.index(t) for t in PCA_TAGS]
    rows = []
    for seed in TRAIN_SEEDS:
        _, vals = run(PlantSimulator(seed=seed, strategy=strategy), minutes)
        sm = EWMA()
        rows.append(np.array([sm(v[idx]) for v in vals])[30:])  # skip the smoother's start-up
    return PCAModel.fit(np.vstack(rows), list(PCA_TAGS))


_CACHE: dict[str, PCAModel] = {}


def default_model(strategy: str = "adaptive") -> PCAModel:
    """The fitted model for a dosing strategy, built once per process (a few seconds) and cached on disk."""
    import json

    from ..paths import CACHE

    if strategy in _CACHE:
        return _CACHE[strategy]
    import hashlib

    src = Path(__file__).resolve().parent.parent / "sim"
    blob = "".join((src / f).read_text(encoding="utf-8") for f in ("process.py", "control.py", "faults.py"))
    key = hashlib.sha1(f"{PCA_TAGS}{TRAIN_SEEDS}{blob}".encode()).hexdigest()[:10]
    path = CACHE / f"pca-{strategy}-{key}.json"  # retrained whenever the simulator changes
    if path.exists():
        model = PCAModel.from_dict(json.loads(path.read_text()))
    else:
        model = train_default(strategy)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(model.to_dict()))
    _CACHE[strategy] = model
    return model
