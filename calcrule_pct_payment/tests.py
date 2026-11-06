from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase

from calcrule_pct_payment.calculation_rule import PctPaymentCalculationRule


class PctPaymentCalculationRuleTest(TestCase):
    def _individual(self, dob, json_ext=None):
        return SimpleNamespace(dob=dob, json_ext=json_ext or {}, is_deleted=False)

    def _group_individual(self, individual):
        return SimpleNamespace(individual=individual, is_deleted=False)

    def test_household_breakdown_applies_caps_and_overlap(self):
        members = [
            self._individual(date(2021, 1, 1), {"json_ext": {"raw": {"primary": "4", "secondary": "5", "disability_hearing": "1"}}}),
            self._individual(date(2022, 1, 1), {"json_ext": {"raw": {"primary": "4", "secondary": "5"}}}),
            self._individual(date(2023, 1, 1), {"json_ext": {"raw": {"primary": "4", "secondary": "5"}}}),
            self._individual(date(2020, 1, 1), {"json_ext": {"raw": {"primary": "4", "secondary": "5"}}}),
        ]
        beneficiary = SimpleNamespace(
            group=SimpleNamespace(groupindividuals=SimpleNamespace(all=lambda: [self._group_individual(m) for m in members]))
        )
        params = {
            "base_amount": Decimal("14000"),
            "disability_top_up": Decimal("5000"),
            "young_child_unit": Decimal("7000"),
            "young_child_age_min": 0,
            "young_child_age_max": 5,
            "young_child_cap_count": 3,
            "primary_unit": Decimal("5000"),
            "primary_cap_count": 3,
            "secondary_unit": Decimal("8000"),
            "secondary_cap_count": 3,
            "household_cap_amount": Decimal("50000"),
            "primary_count_field": "primary",
            "secondary_count_field": "secondary",
        }

        breakdown = PctPaymentCalculationRule._calculate_household_breakdown(
            beneficiary,
            params,
            reference_date=date(2026, 5, 31),
        )

        self.assertEqual(breakdown["young_child_count"], 3)
        self.assertEqual(breakdown["primary_count"], 3)
        self.assertEqual(breakdown["secondary_count"], 3)
        self.assertTrue(breakdown["has_disability"])
        self.assertEqual(breakdown["raw_total"], 64000)
        self.assertEqual(breakdown["capped_total"], 50000)


class MemberValueShapesTest(TestCase):
    def _individual(self, json_ext):
        return SimpleNamespace(dob=None, json_ext=json_ext, is_deleted=False)

    def test_reads_flat_and_nested_raw(self):
        flat = self._individual({"raw": {"primary": "2"}})
        nested = self._individual({"json_ext": {"raw": {"primary": "3"}}})
        both = self._individual({"raw": {"primary": "4"}, "json_ext": {"raw": {"primary": "9"}}})
        get = PctPaymentCalculationRule._get_member_value
        self.assertEqual(get(flat, "primary"), "2")
        self.assertEqual(get(nested, "primary"), "3")
        self.assertEqual(get(both, "primary"), "4")
        self.assertIsNone(get(flat, "secondary"))
