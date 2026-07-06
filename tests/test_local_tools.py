"""local_tools: time, date, converter, calculator, uuid, random."""

import re
import uuid

from local_tools import (
    calculator,
    current_date,
    current_time,
    generate_uuid,
    random_generator,
    resolve_timezone,
    unit_converter,
)


class TestTimezones:
    def test_english_city(self):
        assert str(resolve_timezone("Moscow")) == "Europe/Moscow"

    def test_russian_city(self):
        assert str(resolve_timezone("Стамбул")) == "Europe/Istanbul"

    def test_russian_yo_normalization(self):
        assert resolve_timezone("Кишинёв") is not None

    def test_country(self):
        assert str(resolve_timezone("Япония")) == "Asia/Tokyo"

    def test_iana_literal(self):
        assert str(resolve_timezone("Europe/Berlin")) == "Europe/Berlin"

    def test_iana_lowercase(self):
        assert str(resolve_timezone("europe/berlin")) == "Europe/Berlin"

    def test_utc(self):
        assert str(resolve_timezone("utc")) == "UTC"

    def test_unknown(self):
        assert resolve_timezone("Атлантида") is None


class TestCurrentTime:
    def test_known_city(self):
        out = current_time("Moscow")
        assert "Time:" in out and "Date:" in out and "Weekday:" in out
        assert "Europe/Moscow" in out
        assert "UTC+03:00" in out

    def test_unknown_location_message(self):
        out = current_time("Nowhere-Ville-12345")
        assert "Unknown location" in out

    def test_default_local(self):
        out = current_time("")
        assert "Time:" in out


class TestCurrentDate:
    def test_fields(self):
        out = current_date("UTC")
        assert "Date:" in out
        assert "Week of year:" in out
        assert "Day of year:" in out
        assert re.search(r"Date: \d{4}-\d{2}-\d{2}", out)


class TestUnitConverter:
    def test_kg_to_lb(self):
        out = unit_converter(5, "kg", "lb")
        assert "11.0231" in out

    def test_miles_to_km_russian(self):
        out = unit_converter(10, "мили", "км")
        assert "16.0934" in out

    def test_f_to_c(self):
        out = unit_converter(212, "°F", "C")
        assert "= 100" in out

    def test_c_to_k(self):
        out = unit_converter(0, "c", "k")
        assert "273.15" in out

    def test_liters_to_gallons(self):
        out = unit_converter(10, "литры", "gal")
        assert "2.64172" in out

    def test_data_units(self):
        out = unit_converter(1, "gb", "mb")
        assert "= 1024" in out

    def test_unknown_unit_lists_categories(self):
        out = unit_converter(1, "parsec", "km")
        assert "Unknown unit" in out and "length" in out

    def test_category_mismatch(self):
        out = unit_converter(1, "kg", "km")
        assert "different things" in out


class TestCalculator:
    def test_arithmetic(self):
        assert calculator("2 + 2 * 2").endswith("= 6")

    def test_power_caret(self):
        assert calculator("2^10").endswith("= 1024")

    def test_percent(self):
        assert calculator("15% * 2400").endswith("= 360")

    def test_sqrt(self):
        assert calculator("sqrt(144)").endswith("= 12")

    def test_trig(self):
        out = calculator("sin(pi/2)")
        assert out.endswith("= 1")

    def test_factorial(self):
        assert calculator("factorial(5)").endswith("= 120")

    def test_division_by_zero(self):
        assert "division by zero" in calculator("1/0")

    def test_rejects_names(self):
        out = calculator("__import__('os').system('id')")
        assert "Error" in out

    def test_rejects_attribute_access(self):
        assert "Error" in calculator("(1).__class__")

    def test_rejects_huge_exponent(self):
        out = calculator("9**9**9")
        assert "Error" in out

    def test_rejects_huge_factorial(self):
        assert "Error" in calculator("factorial(999999)")

    def test_empty(self):
        assert "Error" in calculator("")

    def test_unknown_function(self):
        assert "unknown function" in calculator("system(1)")

    def test_mod_function(self):
        assert calculator("mod(10, 3)").endswith("= 1")


class TestGenerators:
    def test_uuid_valid(self):
        out = generate_uuid(1)
        uuid.UUID(out)  # raises if invalid

    def test_uuid_count(self):
        lines = generate_uuid(5).splitlines()
        assert len(lines) == 5
        assert len(set(lines)) == 5

    def test_uuid_count_clamped(self):
        assert len(generate_uuid(9999).splitlines()) == 50

    def test_random_number_in_range(self):
        for _ in range(20):
            value = int(random_generator("number", 1, 10))
            assert 1 <= value <= 10

    def test_random_float_in_range(self):
        value = float(random_generator("float", 0, 1))
        assert 0.0 <= value <= 1.0

    def test_random_string_length(self):
        assert len(random_generator("string", length=32)) == 32

    def test_password_has_classes(self):
        pw = random_generator("password", length=16)
        assert len(pw) == 16
        assert any(c.islower() for c in pw)
        assert any(c.isupper() for c in pw)
        assert any(c.isdigit() for c in pw)

    def test_coin(self):
        result = random_generator("coin")
        assert result in ("heads (орёл)", "tails (решка)")

    def test_dice(self):
        out = random_generator("dice", options="2d6")
        assert "total=" in out

    def test_dice_invalid(self):
        assert "Error" in random_generator("dice", options="bad")

    def test_choice(self):
        assert random_generator("choice", options="a, b, c") in ("a", "b", "c")

    def test_choice_empty(self):
        assert "Error" in random_generator("choice", options="")

    def test_unknown_kind(self):
        assert "Unknown kind" in random_generator("nonsense")
