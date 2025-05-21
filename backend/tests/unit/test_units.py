"""Unit-aware numeric comparison: the part that must never guess."""

from __future__ import annotations

import pytest

from app.utils.units import (
    IncompatibleUnitsError,
    canonical_unit,
    compare,
    convert,
    dimension_of,
    parse_quantity,
)


class TestCanonicalisation:
    @pytest.mark.parametrize(
        "written",
        ["N·m", "Nm", "N-m", "N.m", "N m", "n·m", " NM "],
    )
    def test_torque_spellings_agree(self, written: str) -> None:
        assert canonical_unit(written) == "nm"
        assert dimension_of(written) == "torque"

    @pytest.mark.parametrize("written", ["lbf-ft", "lbf·ft", "lbf ft"])
    def test_imperial_torque_spellings_agree(self, written: str) -> None:
        assert dimension_of(written) == "torque"

    def test_degree_forms(self) -> None:
        assert canonical_unit("°C") == "degc"
        assert canonical_unit("deg C") == "degc"
        assert dimension_of("°F") == "temperature"

    def test_slash_is_not_silently_dropped(self) -> None:
        """'m/s' must never collapse onto 'ms' (milliseconds)."""
        assert canonical_unit("m/s") != canonical_unit("ms")
        assert dimension_of("ms") == "time"
        assert dimension_of("m/s") is None  # unknown, not misread


class TestConversion:
    def test_torque_conversion(self) -> None:
        assert convert(9.0, "lbf-ft", "N·m") == pytest.approx(12.2024, rel=1e-4)

    def test_temperature_offset_is_applied(self) -> None:
        assert convert(212.0, "°F", "°C") == pytest.approx(100.0, abs=1e-6)

    def test_incompatible_dimensions_raise(self) -> None:
        with pytest.raises(IncompatibleUnitsError):
            convert(1.0, "kg", "mm")

    def test_unknown_unit_raises_rather_than_assuming(self) -> None:
        with pytest.raises(IncompatibleUnitsError):
            convert(1.0, "widgets", "mm")


class TestCompare:
    def test_between_in_spec(self) -> None:
        result = compare(12.2, "N·m", "between", 11.0, 13.0, "N·m")
        assert result.in_spec is True
        assert result.deviation == 0.0

    def test_between_out_of_spec_reports_distance_outside_band(self) -> None:
        result = compare(14.8, "N·m", "between", 11.0, 13.0, "N·m")
        assert result.in_spec is False
        assert result.deviation == pytest.approx(1.8)

    def test_imperial_value_converted_before_comparison(self) -> None:
        result = compare(9.0, "lbf-ft", "between", 11.0, 13.0, "N·m")
        assert result.in_spec is True
        assert result.converted is True
        assert "converted" in result.note.lower()

    def test_nominal_with_tolerance(self) -> None:
        inside = compare(25.1, "mm", "==", 25.0, None, "mm", tolerance=0.3)
        outside = compare(25.5, "mm", "==", 25.0, None, "mm", tolerance=0.3)
        assert inside.in_spec is True
        assert outside.in_spec is False
        assert outside.deviation == pytest.approx(0.2)

    def test_minimum_limit(self) -> None:
        assert compare(60.0, "%", ">=", 60.0, None, "%").in_spec is True
        assert compare(45.0, "%", ">=", 60.0, None, "%").in_spec is False

    def test_mismatched_dimension_raises_instead_of_coercing(self) -> None:
        with pytest.raises(IncompatibleUnitsError):
            compare(0.11, "kg", "between", 0.05, 0.15, "mm")


class TestParseQuantity:
    @pytest.mark.parametrize(
        ("text", "value", "unit_dim"),
        [
            ("12.5 N·m", 12.5, "torque"),
            ("180 °C", 180.0, "temperature"),
            ("45%", 45.0, "ratio"),
        ],
    )
    def test_parses_value_and_unit(self, text: str, value: float, unit_dim: str) -> None:
        quantity = parse_quantity(text)
        assert quantity is not None
        assert quantity[0] == pytest.approx(value)
        assert dimension_of(quantity[1]) == unit_dim
