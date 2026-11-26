# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Wind farm model: hub-height wind -> per-unit plant output.

Pipeline (every step an option, defaults in :class:`WindOptions`):

1. **Hub-height wind** (:func:`shear_extrapolate`): log law, power law
   (fixed or fitted from two levels), or a month × hour shear ratio
   fitted on a multi-height model (WRF) and applied to a long series.
2. **Air density** (:func:`air_density`, :func:`density_normalise`): the
   IEC 61400-12-1 normalisation of a pitch-regulated turbine,
   ``v_n = v (ρ/ρ0)^(1/3)``, ρ0 = 1.225 kg/m³.  Chilean farms sit from
   sea level to > 2000 m, where ρ is 20 % lower.
3. **Power curve** (:class:`PowerCurve`): a generic curve from the
   turbine's specific power (W/m² of rotor) or a tabulated CSV curve.
4. **Farm smoothing** (:func:`smooth_curve`): the multi-turbine curve
   of Nørgaard & Holttinen (2004) as a Gaussian convolution of the
   single-turbine curve with σ(v) = a + b·v (Renewables.ninja:
   a = 0.6 m/s, b = 0.2; Staffell & Pfenninger 2016).  It models the
   spread of wind speeds across a large area (a fleet or a region), so
   the default for a single-plant profile is ``none``; use ``ninja`` for
   fleet aggregates.
5. **Wake** (:func:`wake_deficit_for_loss`): a uniform wind-speed
   deficit, solved so that the farm loses ``wake_loss`` of its energy.
   Wakes therefore cost energy at partial load and nothing above rated
   speed, as observed (a constant power factor would cap the farm at
   ``1 − wake_loss``); ``wake_mode = power`` keeps the constant factor.
6. **High-wind cut-out** (:func:`apply_cutout`): ``hard`` (curve to 0 at
   ``cut_out``), ``hysteresis`` (stops at ``cut_out``, restarts below
   ``restart``), ``soft`` (storm control: linear ramp-down from
   ``cut_out`` to ``cut_out + soft_width``).
7. **Availability and electrical losses**: multiplicative.
8. **Cap** (``cap``): an explicit maximum per-unit output; farms rarely
   reach nameplate as a whole (availability, collector-grid and
   transformer losses, grid-code headroom).  ``cap`` applies last.

All outputs are per-unit of the plant's nameplate (``Generator.capacity``).
"""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

RHO0 = 1.225  # kg/m3, IEC 61400-12-1 reference density
R_DRY = 287.05  # J/(kg K), dry air
SHEAR_METHODS = ("power", "log", "fitted", "ratio", "none")
DENSITY_MODES = ("weather", "elevation", "none")
SMOOTHING_MODES = ("ninja", "atlite", "gaussian", "none")
WAKE_MODES = ("speed", "power")
CUTOUT_MODES = ("hard", "hysteresis", "soft")
PMAX_BASES = ("gross", "net")

#: Generic turbine classes: specific power [W/m2] (rated power / swept
#: area).  Values are typical of each IEC 61400-1 class of the 2010-2025
#: fleet (e.g. Vestas V112-3.3 = 335, V126-3.45 = 277, V136-3.45 = 237,
#: SG 5.0-145 = 303, V150-4.2 = 238 W/m2).  ``sp<NNN>`` gives any value.
TURBINE_CLASSES: dict[str, float] = {
    "iec1": 400.0,  # high wind, small rotor
    "iec2": 330.0,
    "iec3": 260.0,
    "iec3s": 210.0,  # modern low-specific-power rotors
}


@dataclass(frozen=True)
class PowerCurve:
    """A turbine power curve on the reference density ``RHO0``.

    ``power`` is per-unit of rated power at ``speed`` (m/s, ascending);
    zero outside the table and at/above ``cut_out``.
    """

    speed: FloatArray
    power: FloatArray
    cut_out: float = 25.0
    name: str = "custom"

    def __post_init__(self) -> None:
        if self.speed.ndim != 1 or self.speed.shape != self.power.shape:
            raise ValueError("power curve: speed and power must be 1-D, same size")
        if self.speed.size < 2 or np.any(np.diff(self.speed) <= 0):
            raise ValueError("power curve: speeds must be strictly ascending")
        if np.any(self.power < 0) or np.any(self.power > 1.0 + 1e-9):
            raise ValueError("power curve: per-unit power must be in [0, 1]")

    def __call__(self, v: FloatArray) -> FloatArray:
        """Per-unit power at wind speeds ``v``."""
        v = np.asarray(v, dtype=float)
        out = np.interp(v, self.speed, self.power, left=0.0, right=0.0)
        return np.where(v >= self.cut_out, 0.0, out)

    @property
    def rated_speed(self) -> float:
        """Lowest speed reaching 99.9 % of the curve's maximum."""
        k = int(np.argmax(self.power >= 0.999 * np.max(self.power)))
        return float(self.speed[k])


def generic_curve(
    specific_power: float,
    *,
    cut_in: float = 3.0,
    cut_out: float = 25.0,
    cp: float = 0.45,
    step: float = 0.05,
    name: str | None = None,
) -> PowerCurve:
    """Generic single-turbine curve from the specific power ``SP`` [W/m²].

    Rated speed ``v_r = (2 SP / (ρ0 Cp))^(1/3)`` (``Cp`` the aerodynamic
    efficiency up to rated, 0.45 default); between cut-in and rated the
    power follows ``(v³ − v_in³)/(v_r³ − v_in³)``, the cubic law with a
    continuous start at cut-in.  E.g. SP 330 W/m² -> v_r ≈ 10.6 m/s.
    """
    if specific_power <= 0 or not 0 < cp < 16 / 27:
        raise ValueError("generic curve: SP > 0 and 0 < Cp < Betz (0.593)")
    v_r = (2.0 * specific_power / (RHO0 * cp)) ** (1.0 / 3.0)
    if not 0 <= cut_in < v_r < cut_out:
        raise ValueError(
            f"generic curve: need cut_in < rated ({v_r:.1f}) < cut_out m/s"
        )
    v = np.arange(0.0, cut_out + step / 2, step, dtype=np.float64)
    p = np.clip((v**3 - cut_in**3) / (v_r**3 - cut_in**3), 0.0, 1.0)
    p[v < cut_in] = 0.0
    return PowerCurve(
        v, np.asarray(p, dtype=np.float64), cut_out, name or f"sp{specific_power:g}"
    )


def read_curve_csv(path: Path, cut_out: float | None = None) -> PowerCurve:
    """Tabulated curve: CSV ``speed,power`` (power in kW, MW or p.u.: it is
    divided by its maximum).  ``cut_out`` defaults to the last speed with
    power, plus 0.5 m/s if the table stops there."""
    with path.open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r]
    if not rows or not {"speed", "power"} <= set(rows[0]):
        raise ValueError(f"{path}: power curve CSV needs columns speed,power")
    v = np.array([float(r["speed"]) for r in rows])
    p = np.array([float(r["power"]) for r in rows])
    if p.max() <= 0:
        raise ValueError(f"{path}: power curve has no positive power")
    p = np.clip(p / p.max(), 0.0, 1.0)
    if cut_out is None:
        cut_out = float(v[np.flatnonzero(p > 0)[-1]]) + 0.5
    return PowerCurve(v, p, cut_out, path.stem)


def turbine_curve(
    spec: str,
    *,
    cut_in: float = 3.0,
    cut_out: float = 25.0,
    base_dir: Path | None = None,
) -> PowerCurve:
    """Curve from a spec: a class (``iec1``, ``iec2``, ``iec3``, ``iec3s``),
    ``sp<W/m2>`` (e.g. ``sp300``) or a CSV path (``speed,power``)."""
    s = spec.strip()
    key = s.lower()
    if key in TURBINE_CLASSES:
        return generic_curve(
            TURBINE_CLASSES[key], cut_in=cut_in, cut_out=cut_out, name=key
        )
    if key.startswith("sp"):
        try:
            sp = float(key[2:])
        except ValueError as exc:
            raise ValueError(f"turbine {spec!r}: use sp<W/m2>, e.g. sp300") from exc
        return generic_curve(sp, cut_in=cut_in, cut_out=cut_out)
    path = Path(s)
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    if path.suffix.lower() == ".csv" and path.exists():
        return read_curve_csv(path, cut_out)
    raise ValueError(
        f"turbine {spec!r}: use {sorted(TURBINE_CLASSES)}, sp<W/m2> or a CSV file"
    )


def specific_power(turbine_mw: float, rotor_diameter_m: float) -> float:
    """Specific power [W/m²] of a turbine."""
    if turbine_mw <= 0 or rotor_diameter_m <= 0:
        raise ValueError("specific power: positive turbine MW and rotor diameter")
    return turbine_mw * 1e6 / (math.pi * rotor_diameter_m**2 / 4.0)


# ---------------------------------------------------------------------------
# farm smoothing (multi-turbine power curve)
# ---------------------------------------------------------------------------


def smooth_curve(
    curve: PowerCurve, a: float = 0.6, b: float = 0.2, v_max: float = 40.0
) -> PowerCurve:
    """Multi-turbine curve: the expectation of the single curve over a
    Gaussian wind-speed spread σ(v) = a + b·v around the farm-mean speed
    (Nørgaard & Holttinen 2004; Staffell & Pfenninger 2016 use a = 0.6,
    b = 0.2).  The spread stands for the speeds seen by turbines across
    the farm and within the hour; it rounds the knee at rated speed and
    turns the cut-out step into a ramp.  ``a = b = 0`` returns the
    curve unchanged."""
    if a < 0 or b < 0:
        raise ValueError("smoothing: a, b must be >= 0")
    if a == 0 and b == 0:
        return curve
    step = float(np.min(np.diff(curve.speed)))
    grid = np.arange(0.0, v_max + step / 2, step, dtype=np.float64)
    p_fine = curve(grid)
    out = np.zeros_like(grid)
    for i, v in enumerate(grid):
        sig = a + b * v
        w = np.exp(-0.5 * ((grid - v) / sig) ** 2)
        s = w.sum()
        out[i] = float((w * p_fine).sum() / s) if s > 0 else 0.0
    # the smoothed curve already ramps down through cut-out
    return PowerCurve(
        grid,
        np.asarray(np.clip(out, 0.0, 1.0), dtype=np.float64),
        v_max + 1.0,
        curve.name + "+sm",
    )


def atlite_smooth(
    curve: PowerCurve,
    delta_v: float = 1.27,
    sigma: float = 2.29,
    eta: float = 0.95,
    v_max: float = 40.0,
) -> PowerCurve:
    """atlite's ``windturbine_smooth`` (Andresen et al. 2015): convolve
    the curve with a Gaussian of mean ``delta_v`` and constant ``sigma``
    (m/s), then scale by the fleet availability ``eta``.  Fitted on the
    Danish fleet; it shifts the curve to higher speeds and caps it at
    ``eta``.  Kept as an alternative to the Renewables.ninja smoothing."""
    step = float(np.min(np.diff(curve.speed)))
    grid = np.arange(0.0, v_max + step / 2, step, dtype=np.float64)
    p_fine = curve(grid)
    out = np.zeros_like(grid)
    for i, v in enumerate(grid):
        # P_s(v) = E[P(v - X)], X ~ N(delta_v, sigma)
        w = np.exp(-0.5 * ((v - grid - delta_v) / sigma) ** 2)
        s = w.sum()
        out[i] = float((w * p_fine).sum() / s) if s > 0 else 0.0
    return PowerCurve(
        grid,
        np.asarray(np.clip(eta * out, 0.0, 1.0), dtype=np.float64),
        v_max + 1.0,
        curve.name + "+atlite",
    )


# ---------------------------------------------------------------------------
# hub height
# ---------------------------------------------------------------------------


def shear_extrapolate(
    levels: Mapping[float, FloatArray],
    hub_height: float,
    method: str = "power",
    *,
    alpha: float = 1.0 / 7.0,
    z0: float = 0.03,
    alpha_clip: tuple[float, float] = (0.0, 0.6),
    ratio: FloatArray | None = None,
) -> FloatArray:
    """Wind speed at ``hub_height`` from the speeds at one or more levels.

    ``power``  ``v(z) = v(z_r) (z/z_r)^α`` from the highest level, α fixed
               (default 1/7, the neutral open-terrain value);
    ``log``    ``v(z) = v(z_r) ln(z/z0) / ln(z_r/z0)`` (neutral log law);
    ``fitted`` α fitted hour by hour from the two outermost levels
               (``ln(v2/v1)/ln(z2/z1)``, clipped to ``alpha_clip``);
               near-surface pairs (10/20 m) are noisy, so clip;
    ``ratio``  ``v(z) = v(z_r) · ratio`` with ``ratio`` per hour (e.g. a
               month × hour ratio v_hub/v_ref fitted on a multi-height
               model, see :func:`fit_shear_ratio`);
    ``none``   linear interpolation between the bracketing levels (the
               level itself when it exists; no extrapolation beyond).
    """
    if method not in SHEAR_METHODS:
        raise ValueError(f"shear method must be one of {SHEAR_METHODS}")
    if not levels:
        raise ValueError("shear: no wind levels")
    hs = sorted(levels)
    z_r = hs[-1]
    v_r = np.asarray(levels[z_r], dtype=float)
    if hub_height in levels:
        return np.maximum(np.asarray(levels[hub_height], dtype=float), 0.0)
    if method == "none":
        if hub_height >= z_r:
            return np.maximum(v_r, 0.0)
        if hub_height <= hs[0]:
            return np.maximum(np.asarray(levels[hs[0]], dtype=float), 0.0)
        hi = min(h for h in hs if h > hub_height)
        lo = max(h for h in hs if h < hub_height)
        w = (hub_height - lo) / (hi - lo)
        out = (1 - w) * np.asarray(levels[lo]) + w * np.asarray(levels[hi])
        return np.maximum(out, 0.0)
    if method == "power":
        return np.maximum(v_r * (hub_height / z_r) ** alpha, 0.0)
    if method == "log":
        if z0 <= 0 or z_r <= z0:
            raise ValueError("log law: need 0 < z0 < reference height")
        return np.maximum(v_r * math.log(hub_height / z0) / math.log(z_r / z0), 0.0)
    if method == "ratio":
        if ratio is None:
            raise ValueError("shear ratio: no ratio series given")
        return np.maximum(v_r * np.asarray(ratio, dtype=float), 0.0)
    # fitted
    if len(hs) < 2:
        raise ValueError("fitted shear needs two levels")
    z1 = hs[0]
    v1 = np.asarray(levels[z1], dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        a = np.log(v_r / v1) / math.log(z_r / z1)
    a = np.clip(np.nan_to_num(a, nan=alpha, posinf=alpha, neginf=alpha), *alpha_clip)
    return np.maximum(v_r * (hub_height / z_r) ** a, 0.0)


def fit_shear_ratio(
    v_ref: FloatArray,
    v_hub: FloatArray,
    month: npt.NDArray[np.int64],
    hour: npt.NDArray[np.int64],
    clip: tuple[float, float] = (0.8, 3.0),
) -> FloatArray:
    """(12, 24) month × hour ratio ``Σ v_hub / Σ v_ref`` (ratio of means,
    robust at calm hours), clipped; 1 where a bin is empty.  ``month``
    1-12, ``hour`` 0-23 (local or UTC, but the same when applied)."""
    out = np.ones((12, 24))
    for m in range(1, 13):
        for h in range(24):
            sel = (month == m) & (hour == h)
            s = float(np.sum(v_ref[sel]))
            if sel.any() and s > 1e-6:
                out[m - 1, h] = float(np.clip(np.sum(v_hub[sel]) / s, *clip))
    return out


# ---------------------------------------------------------------------------
# density
# ---------------------------------------------------------------------------


def pressure_at(elevation_m: float) -> float:
    """ISA pressure [Pa] at an elevation (barometric formula)."""
    return 101325.0 * (1.0 - 2.25577e-5 * elevation_m) ** 5.25588


def air_density(
    temperature_c: FloatArray | float,
    pressure_pa: FloatArray | float | None = None,
    elevation_m: float = 0.0,
) -> FloatArray:
    """Dry-air density ρ = p / (R T) [kg/m³]; ``pressure_pa`` defaults to
    the ISA pressure at ``elevation_m`` (humidity is neglected: < 1 %)."""
    t = np.asarray(temperature_c, dtype=float) + 273.15
    p = pressure_at(elevation_m) if pressure_pa is None else pressure_pa
    return np.asarray(np.asarray(p, dtype=float) / (R_DRY * t), dtype=float)


def density_normalise(v: FloatArray, rho: FloatArray | float) -> FloatArray:
    """IEC 61400-12-1 (pitch-regulated): ``v_n = v (ρ/ρ0)^(1/3)``, the
    speed at which the reference-density curve gives the same power."""
    return np.asarray(v, dtype=float) * (np.asarray(rho, dtype=float) / RHO0) ** (
        1.0 / 3.0
    )


# ---------------------------------------------------------------------------
# cut-out
# ---------------------------------------------------------------------------


def apply_cutout(
    v: FloatArray,
    p: FloatArray,
    mode: str = "hard",
    *,
    cut_out: float = 25.0,
    restart: float = 20.0,
    soft_width: float = 5.0,
) -> FloatArray:
    """High-wind behaviour on an hourly series (``v`` hub speed, ``p`` the
    curve output).  ``hard``: the curve's own cut-out (unchanged);
    ``hysteresis``: 0 from the first hour ≥ ``cut_out`` until the speed
    falls below ``restart``; ``soft``: ``p`` ramps linearly to 0 between
    ``cut_out`` and ``cut_out + soft_width`` (storm control)."""
    if mode not in CUTOUT_MODES:
        raise ValueError(f"cut-out mode must be one of {CUTOUT_MODES}")
    v = np.asarray(v, dtype=float)
    p = np.asarray(p, dtype=float).copy()
    if mode == "hard":
        return p
    if mode == "soft":
        f = np.clip((cut_out + soft_width - v) / soft_width, 0.0, 1.0)
        return np.where(v >= cut_out, p * f, p)
    if restart >= cut_out:
        raise ValueError("hysteresis: restart must be below cut_out")
    stopped = False
    for i, vi in enumerate(v):
        if vi >= cut_out:
            stopped = True
        elif stopped and vi < restart:
            stopped = False
        if stopped:
            p[i] = 0.0
    return p


# ---------------------------------------------------------------------------
# farm model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindOptions:
    """Every assumption of the wind model, with its documented default."""

    turbine: str = "auto"
    cut_in: float = 3.0
    cut_out: float = 25.0
    restart: float = 20.0
    cutout_mode: str = "soft"
    soft_width: float = 5.0
    smoothing: str = "none"
    smoothing_a: float = 0.6
    smoothing_b: float = 0.2
    density: str = "weather"
    wake_loss: float = 0.07
    wake_mode: str = "speed"
    availability: float = 0.97
    electrical_loss: float = 0.02
    cap: float | None = 0.95
    extra: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        """Raise on an invalid option."""
        for name, val, allowed in (
            ("smoothing", self.smoothing, SMOOTHING_MODES),
            ("density", self.density, DENSITY_MODES),
            ("wake_mode", self.wake_mode, WAKE_MODES),
            ("cutout_mode", self.cutout_mode, CUTOUT_MODES),
        ):
            if val not in allowed:
                raise ValueError(f"wind {name} must be one of {allowed}")
        for name, frac in (
            ("wake_loss", self.wake_loss),
            ("electrical_loss", self.electrical_loss),
        ):
            if not 0.0 <= frac < 1.0:
                raise ValueError(f"wind {name} must be in [0, 1)")
        if not 0.0 < self.availability <= 1.0:
            raise ValueError("wind availability must be in (0, 1]")
        if self.cap is not None and not 0.0 < self.cap <= 1.0:
            raise ValueError("wind cap must be in (0, 1]")

    def with_overrides(self, over: Mapping[str, object]) -> WindOptions:
        """A copy with per-plant overrides (unknown keys ignored)."""
        known = {k: v for k, v in over.items() if k in _WIND_FIELDS and v is not None}
        return replace(self, **known)  # type: ignore[arg-type]


_WIND_FIELDS = frozenset(f.name for f in fields(WindOptions)) - {"extra"}


def farm_curve(opt: WindOptions, base_dir: Path | None = None) -> PowerCurve:
    """The (smoothed) farm curve at reference density (``auto`` and
    ``oedb:`` types are resolved by ``re2gtopt._model.resolve_curve``;
    here ``auto`` means the generic ``iec2`` class)."""
    spec = "iec2" if opt.turbine == "auto" else opt.turbine
    curve = turbine_curve(
        spec, cut_in=opt.cut_in, cut_out=opt.cut_out, base_dir=base_dir
    )
    if opt.smoothing == "none":
        return curve
    if opt.smoothing == "ninja":
        return smooth_curve(curve, 0.6, 0.2)
    if opt.smoothing == "atlite":
        return atlite_smooth(curve)
    return smooth_curve(curve, opt.smoothing_a, opt.smoothing_b)


def wake_deficit_for_loss(
    curve: PowerCurve,
    v: FloatArray,
    loss: float,
    tol: float = 1e-6,
    max_deficit: float = 0.3,
) -> float:
    """Uniform speed deficit ``d`` with mean P(v(1−d)) = (1−loss)·mean P(v)
    (bisection; P is monotone up to rated), at most ``max_deficit``."""
    if loss <= 0:
        return 0.0
    base = float(np.mean(curve(v))) if np.size(v) else 0.0
    if base <= 1e-6:
        return 0.0
    target = (1.0 - loss) * base
    lo, hi = 0.0, max_deficit
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if float(np.mean(curve(v * (1.0 - mid)))) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


@dataclass
class WindResult:
    """Per-unit output of a wind farm and the diagnostics of each step."""

    cf: FloatArray
    hub_speed: FloatArray
    effective_speed: FloatArray
    wake_deficit: float
    curve: PowerCurve
    diagnostics: dict[str, float] = field(default_factory=dict)


def _finite_max(a: FloatArray) -> float:
    f = a[np.isfinite(a)]
    return float(np.max(f)) if f.size else 0.0


def _finite_mean(a: FloatArray, default: float) -> float:
    f = np.asarray(a, dtype=float)
    f = f[np.isfinite(f)]
    return float(f.mean()) if f.size else default


def wind_farm_cf(
    hub_speed: FloatArray,
    opt: WindOptions,
    *,
    temperature_c: FloatArray | None = None,
    pressure_pa: FloatArray | None = None,
    elevation_m: float = 0.0,
    speed_scale: float = 1.0,
    speed_offset: float = 0.0,
    base_dir: Path | None = None,
    curve: PowerCurve | None = None,
    installed_ratio: float = 1.0,
    pmax_basis: str = "gross",
) -> WindResult:
    """Per-unit (of the reported Pmax) farm output from the hub-height
    wind speed series.

    ``speed_scale``/``speed_offset`` are the bias-correction parameters
    (``v' = scale·v + offset``, Renewables.ninja style), 1/0 = none.
    ``installed_ratio`` = Σ turbine ratings / Pmax (rounding of the
    turbine count, or a CEN equipment list that disagrees with Pmax).
    The plant never exceeds its Pmax (ceiling 1.0 p.u.), and ``cap``
    applies after it as a separate factor.  ``pmax_basis`` says whether
    Pmax is ``gross`` (turbine terminals) or ``net`` (after electrical
    losses); it only changes the reported ``peak_pu`` consistency check.
    """
    opt.validate()  # hours with missing wind (NaN) stay NaN in the output
    if pmax_basis not in PMAX_BASES:
        raise ValueError(f"pmax basis must be one of {PMAX_BASES}")
    v = np.maximum(np.asarray(hub_speed, dtype=float) * speed_scale + speed_offset, 0)
    curve = curve if curve is not None else farm_curve(opt, base_dir)
    if opt.density == "none":
        vn = v
        rho = np.full(v.shape, RHO0)
    elif opt.density == "elevation" or temperature_c is None:
        t = (
            np.full(v.shape, 15.0 - 0.0065 * elevation_m)
            if temperature_c is None
            else temperature_c
        )
        rho = air_density(t, None, elevation_m)
        vn = density_normalise(v, rho)
    else:
        rho = air_density(temperature_c, pressure_pa, elevation_m)
        vn = density_normalise(v, rho)
    deficit = 0.0
    if opt.wake_mode == "speed":
        deficit = wake_deficit_for_loss(curve, vn[np.isfinite(vn)], opt.wake_loss)
    v_eff = vn * (1.0 - deficit)
    p = curve(v_eff)
    if opt.wake_mode == "power":
        p = p * (1.0 - opt.wake_loss)
    # cut-out acts on the free-stream hub speed (the turbines' anemometers)
    p = apply_cutout(
        v,
        p,
        opt.cutout_mode,
        cut_out=opt.cut_out,
        restart=opt.restart,
        soft_width=opt.soft_width,
    )
    p = p * installed_ratio
    # consistency check quantity: the peak before availability and cap,
    # on the basis of the reported Pmax (net -> after electrical losses)
    peak_basis = p * (1.0 - opt.electrical_loss) if pmax_basis == "net" else p
    peak_pu = _finite_max(np.minimum(peak_basis, 1.0))
    p = np.minimum(p * opt.availability * (1.0 - opt.electrical_loss), 1.0)
    uncapped_max = _finite_max(p)
    if opt.cap is not None:
        p = np.minimum(p, opt.cap)
    diag = {
        "mean_rho": _finite_mean(rho, RHO0),
        "mean_hub_speed": _finite_mean(v, 0.0),
        "wake_deficit": deficit,
        "uncapped_max": uncapped_max,
        "peak_pu": peak_pu,
        "installed_ratio": installed_ratio,
        "capped_hours": float(np.sum(p >= (opt.cap or 2.0) - 1e-12)),
        "rated_speed": curve.rated_speed,
    }
    return WindResult(np.clip(p, 0.0, 1.0), v, v_eff, deficit, curve, diag)
