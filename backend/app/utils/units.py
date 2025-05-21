"""Unit-aware numeric handling for deterministic parameter comparison.

A deliberately small, fully auditable unit registry (no heavy dependency) that
covers the dimensions found in the synthetic manufacturing dataset. Conversions
are exact where possible; temperature uses affine conversion.

Rules
-----
* Two quantities are comparable only if they share a *dimension*.
* A value given in a different-but-compatible unit is converted, and the
  conversion is reported so the audit trail shows what was compared.
* An unknown or dimension-incompatible unit is **never** silently coerced; it
  raises :class:`IncompatibleUnitsError` and the engine emits a
  ``unit_mismatch`` finding instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TOLERANCE_EPS = 1e-9


class IncompatibleUnitsError(ValueError):
    """Raised when two quantities cannot be compared."""


# unit -> (dimension, factor to base, offset)
_UNITS: dict[str, tuple[str, float, float]] = {
    # length (base: mm)
    "mm": ("length", 1.0, 0.0),
    "millimetre": ("length", 1.0, 0.0),
    "millimeter": ("length", 1.0, 0.0),
    "cm": ("length", 10.0, 0.0),
    "m": ("length", 1000.0, 0.0),
    "metre": ("length", 1000.0, 0.0),
    "meter": ("length", 1000.0, 0.0),
    "um": ("length", 0.001, 0.0),
    "micron": ("length", 0.001, 0.0),
    "in": ("length", 25.4, 0.0),
    "inch": ("length", 25.4, 0.0),
    "inches": ("length", 25.4, 0.0),
    "ft": ("length", 304.8, 0.0),
    # mass (base: g)
    "g": ("mass", 1.0, 0.0),
    "gram": ("mass", 1.0, 0.0),
    "grams": ("mass", 1.0, 0.0),
    "kg": ("mass", 1000.0, 0.0),
    "mg": ("mass", 0.001, 0.0),
    "lb": ("mass", 453.59237, 0.0),
    "lbs": ("mass", 453.59237, 0.0),
    "oz": ("mass", 28.349523125, 0.0),
    # temperature (base: degC, affine)
    "c": ("temperature", 1.0, 0.0),
    "degc": ("temperature", 1.0, 0.0),
    "celsius": ("temperature", 1.0, 0.0),
    "f": ("temperature", 5.0 / 9.0, -32.0),
    "degf": ("temperature", 5.0 / 9.0, -32.0),
    "fahrenheit": ("temperature", 5.0 / 9.0, -32.0),
    "k": ("temperature", 1.0, -273.15),
    "kelvin": ("temperature", 1.0, -273.15),
    # pressure (base: kPa)
    "kpa": ("pressure", 1.0, 0.0),
    "pa": ("pressure", 0.001, 0.0),
    "mpa": ("pressure", 1000.0, 0.0),
    "bar": ("pressure", 100.0, 0.0),
    "mbar": ("pressure", 0.1, 0.0),
    "psi": ("pressure", 6.894757293168361, 0.0),
    # torque (base: N·m)
    "nm": ("torque", 1.0, 0.0),
    "n.m": ("torque", 1.0, 0.0),
    "n-m": ("torque", 1.0, 0.0),
    "newtonmetre": ("torque", 1.0, 0.0),
    "ncm": ("torque", 0.01, 0.0),
    "lbfft": ("torque", 1.3558179483314004, 0.0),
    "lbft": ("torque", 1.3558179483314004, 0.0),
    "ftlb": ("torque", 1.3558179483314004, 0.0),
    "ftlbs": ("torque", 1.3558179483314004, 0.0),
    "lbfin": ("torque", 0.1129848290276167, 0.0),
    "inlb": ("torque", 0.1129848290276167, 0.0),
    "kgfcm": ("torque", 0.0980665, 0.0),
    # time (base: s)
    "s": ("time", 1.0, 0.0),
    "sec": ("time", 1.0, 0.0),
    "secs": ("time", 1.0, 0.0),
    "second": ("time", 1.0, 0.0),
    "seconds": ("time", 1.0, 0.0),
    "min": ("time", 60.0, 0.0),
    "mins": ("time", 60.0, 0.0),
    "minute": ("time", 60.0, 0.0),
    "minutes": ("time", 60.0, 0.0),
    "h": ("time", 3600.0, 0.0),
    "hr": ("time", 3600.0, 0.0),
    "hour": ("time", 3600.0, 0.0),
    "hours": ("time", 3600.0, 0.0),
    "ms": ("time", 0.001, 0.0),
    # volume (base: mL)
    "ml": ("volume", 1.0, 0.0),
    "l": ("volume", 1000.0, 0.0),
    "litre": ("volume", 1000.0, 0.0),
    "liter": ("volume", 1000.0, 0.0),
    "ul": ("volume", 0.001, 0.0),
    # dimensionless / counts
    "%": ("ratio", 1.0, 0.0),
    "percent": ("ratio", 1.0, 0.0),
    "pct": ("ratio", 1.0, 0.0),
    "rpm": ("rotational_speed", 1.0, 0.0),
    "pcs": ("count", 1.0, 0.0),
    "piece": ("count", 1.0, 0.0),
    "pieces": ("count", 1.0, 0.0),
    "units": ("count", 1.0, 0.0),
    "unit": ("count", 1.0, 0.0),
    "ea": ("count", 1.0, 0.0),
    "v": ("voltage", 1.0, 0.0),
    "mv": ("voltage", 0.001, 0.0),
    "kv": ("voltage", 1000.0, 0.0),
    "a": ("current", 1.0, 0.0),
    "ma": ("current", 0.001, 0.0),
    "ohm": ("resistance", 1.0, 0.0),
    "kohm": ("resistance", 1000.0, 0.0),
}

# Separators that carry no meaning inside a unit token. Real records write the same
# torque unit as "N·m", "N-m", "N m", "N.m" or "Nm"; all must canonicalise identically.
_CANON_RE = re.compile(r"[\s·*_\-\.]")


def canonical_unit(unit: str | None) -> str | None:
    """Normalise a raw unit string (``'N·m'`` -> ``'nm'``, ``'°C'`` -> ``'c'``)."""
    if unit is None:
        return None
    u = unit.strip().lower()
    if not u:
        return None
    u = u.replace("°", "deg").replace("º", "deg")
    u = _CANON_RE.sub("", u)
    # A slash must never be deleted: "m/s" would collapse onto "ms" (milliseconds) and
    # silently change dimension. Encode it instead so compound units stay distinct and
    # are reported as unknown rather than silently misread.
    u = u.replace("/", "per")
    if u in {"degc"}:
        return "degc"
    if u in {"degf"}:
        return "degf"
    return u


def dimension_of(unit: str | None) -> str | None:
    cu = canonical_unit(unit)
    if cu is None:
        return "dimensionless"
    entry = _UNITS.get(cu)
    return entry[0] if entry else None


def is_known_unit(unit: str | None) -> bool:
    return canonical_unit(unit) is None or canonical_unit(unit) in _UNITS


def to_base(value: float, unit: str | None) -> tuple[float, str]:
    """Convert ``value`` to its dimension base unit. Unknown units raise."""
    cu = canonical_unit(unit)
    if cu is None:
        return value, "dimensionless"
    entry = _UNITS.get(cu)
    if entry is None:
        raise IncompatibleUnitsError(f"Unknown unit '{unit}'")
    dim, factor, offset = entry
    return (value + offset) * factor, dim


def convert(value: float, from_unit: str | None, to_unit: str | None) -> float:
    """Convert between two units of the same dimension."""
    base_value, from_dim = to_base(value, from_unit)
    cu_to = canonical_unit(to_unit)
    if cu_to is None:
        if from_dim != "dimensionless":
            raise IncompatibleUnitsError(f"Cannot convert {from_unit} to a dimensionless value")
        return base_value
    entry = _UNITS.get(cu_to)
    if entry is None:
        raise IncompatibleUnitsError(f"Unknown unit '{to_unit}'")
    to_dim, factor, offset = entry
    if to_dim != from_dim:
        raise IncompatibleUnitsError(f"Incompatible dimensions: {from_dim} vs {to_dim}")
    return base_value / factor - offset


@dataclass(frozen=True)
class ComparisonResult:
    """Outcome of a deterministic parameter comparison."""

    in_spec: bool
    observed_in_expected_unit: float
    expected_description: str
    deviation: float | None
    deviation_pct: float | None
    converted: bool
    note: str = ""


def compare(
    observed: float,
    observed_unit: str | None,
    comparator: str,
    value_min: float | None,
    value_max: float | None,
    expected_unit: str | None,
    tolerance: float | None = None,
) -> ComparisonResult:
    """Evaluate ``observed`` against an SOP constraint. Pure, deterministic.

    ``comparator`` is one of ``==``, ``!=``, ``>=``, ``<=``, ``>``, ``<``,
    ``between``. ``tolerance`` is expressed in the expected unit.
    """
    converted = False
    obs = observed
    if canonical_unit(observed_unit) != canonical_unit(expected_unit):
        obs = convert(observed, observed_unit, expected_unit)
        converted = True

    lo, hi = value_min, value_max
    if comparator == "between":
        if lo is None or hi is None:
            raise ValueError("'between' requires both bounds")
    elif comparator == "==":
        tol = tolerance or 0.0
        lo, hi = (value_min or 0.0) - tol, (value_min or 0.0) + tol
    unit_suffix = f" {expected_unit}" if expected_unit else ""

    if comparator in {"==", "between"}:
        assert lo is not None and hi is not None
        in_spec = (lo - TOLERANCE_EPS) <= obs <= (hi + TOLERANCE_EPS)
        desc = (
            f"{lo:g}–{hi:g}{unit_suffix}"
            if comparator == "between"
            else f"{value_min:g}{unit_suffix}" + (f" ±{tolerance:g}" if tolerance else "")
        )
        if in_spec:
            deviation = 0.0
        else:
            deviation = obs - hi if obs > hi else obs - lo
    elif comparator == ">=":
        in_spec = obs >= (value_min or 0.0) - TOLERANCE_EPS
        desc = f"≥ {value_min:g}{unit_suffix}"
        deviation = 0.0 if in_spec else obs - (value_min or 0.0)
    elif comparator == ">":
        in_spec = obs > (value_min or 0.0)
        desc = f"> {value_min:g}{unit_suffix}"
        deviation = 0.0 if in_spec else obs - (value_min or 0.0)
    elif comparator == "<=":
        in_spec = obs <= (value_max if value_max is not None else (value_min or 0.0)) + TOLERANCE_EPS
        bound = value_max if value_max is not None else (value_min or 0.0)
        desc = f"≤ {bound:g}{unit_suffix}"
        deviation = 0.0 if in_spec else obs - bound
    elif comparator == "<":
        bound = value_max if value_max is not None else (value_min or 0.0)
        in_spec = obs < bound
        desc = f"< {bound:g}{unit_suffix}"
        deviation = 0.0 if in_spec else obs - bound
    elif comparator == "!=":
        in_spec = abs(obs - (value_min or 0.0)) > TOLERANCE_EPS
        desc = f"≠ {value_min:g}{unit_suffix}"
        # A "must not equal" breach has no meaningful magnitude - it is binary.
        deviation = 0.0
    else:
        raise ValueError(f"Unsupported comparator '{comparator}'")

    reference = value_min if value_min is not None else value_max
    deviation_pct = None
    if deviation is not None and reference not in (None, 0):
        deviation_pct = round(deviation / abs(reference) * 100.0, 3)

    note = ""
    if converted:
        note = f"Observed {observed:g} {observed_unit or ''} converted to {obs:.4g} {expected_unit or ''}".strip()
    return ComparisonResult(
        in_spec=in_spec,
        observed_in_expected_unit=round(obs, 6),
        expected_description=desc,
        deviation=None if deviation is None else round(deviation, 6),
        deviation_pct=deviation_pct,
        converted=converted,
        note=note,
    )


_NUM_UNIT_RE = re.compile(
    r"(?P<num>-?\d+(?:[.,]\d+)?)\s*(?P<unit>%|°\s?[CF]|[A-Za-zµ·\.]{1,10}\b)?",
)


def parse_quantity(text: str) -> tuple[float, str | None] | None:
    """Extract the first ``(value, unit)`` pair from free text."""
    for match in _NUM_UNIT_RE.finditer(text):
        raw_num = match.group("num").replace(",", ".")
        try:
            value = float(raw_num)
        except ValueError:
            continue
        unit = match.group("unit")
        if unit:
            unit = unit.strip()
            if canonical_unit(unit) not in _UNITS:
                unit = None
        return value, unit
    return None
