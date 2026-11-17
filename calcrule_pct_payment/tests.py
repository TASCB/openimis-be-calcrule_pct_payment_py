from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase

from calcrule_pct_payment.calculation_rule import PctPaymentCalculationRule


class PctPaymentCalculationRuleTest(TestCase):
    def _individual(self, dob, json_ext=None):
        return SimpleNamespace(dob=dob, json_ext=json_ext or {}, is_deleted=False)

    def _group_individual(self, individual, is_active=True):
        return SimpleNamespace(individual=individual, is_deleted=False, is_active=is_active)

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
            "children_cap_count": 3,
            "pregnant_woman_unit": Decimal("7000"),
            "pregnant_woman_cap_count": 1,
            "pregnancy_field": "pregnant",
            "primary_count_field": "primary",
            "secondary_count_field": "secondary",
        }

        breakdown = PctPaymentCalculationRule.household_breakdown(
            beneficiary,
            params,
            reference_date=date(2026, 5, 31),
        )

        self.assertEqual(breakdown["young_child_count_raw"], 3)
        self.assertEqual((breakdown["secondary_count"], breakdown["young_child_count"], breakdown["primary_count"]),
                         (3, 0, 0))
        self.assertTrue(breakdown["has_disability"])
        self.assertEqual(breakdown["raw_total"], 43000)
        self.assertEqual(breakdown["capped_total"], 43000)

    def test_deactivated_members_are_not_paid_for(self):
        disabled_child = self._individual(date(2023, 1, 1), {"disability_hearing": "1"})
        child = self._individual(date(2022, 1, 1))
        links = [self._group_individual(disabled_child, is_active=False), self._group_individual(child)]
        beneficiary = SimpleNamespace(group=SimpleNamespace(groupindividuals=SimpleNamespace(all=lambda: links)))
        params = {
            "base_amount": Decimal("14000"), "disability_top_up": Decimal("5000"),
            "young_child_unit": Decimal("7000"), "young_child_age_min": 0, "young_child_age_max": 5,
            "young_child_cap_count": 3, "primary_unit": Decimal("5000"), "primary_cap_count": 3,
            "secondary_unit": Decimal("8000"), "secondary_cap_count": 3, "household_cap_amount": Decimal("50000"),
            "children_cap_count": 3, "primary_count_field": "primary", "secondary_count_field": "secondary",
            "pregnant_woman_unit": Decimal("7000"), "pregnant_woman_cap_count": 1, "pregnancy_field": "pregnant",
        }
        breakdown = PctPaymentCalculationRule.household_breakdown(beneficiary, params, reference_date=date(2026, 5, 31))
        self.assertEqual((breakdown["young_child_count"], breakdown["has_disability"]), (1, False))
        self.assertEqual(breakdown["capped_total"], 21000)

    def test_pregnant_woman_is_paid_once_outside_the_children_cap(self):
        mothers = [self._individual(date(1995, 1, 1), {"pregnant": "1"}),
                   self._individual(date(1997, 1, 1), {"json_ext": {"raw": {"pregnant": "yes"}}})]
        children = [self._individual(date(2023, 1, 1)) for _ in range(3)]
        links = [self._group_individual(m) for m in mothers + children]
        beneficiary = SimpleNamespace(group=SimpleNamespace(groupindividuals=SimpleNamespace(all=lambda: links)))
        params = {
            "base_amount": Decimal("14000"), "disability_top_up": Decimal("5000"),
            "young_child_unit": Decimal("7000"), "young_child_age_min": 0, "young_child_age_max": 5,
            "young_child_cap_count": 3, "primary_unit": Decimal("5000"), "primary_cap_count": 3,
            "secondary_unit": Decimal("8000"), "secondary_cap_count": 3, "household_cap_amount": Decimal("50000"),
            "children_cap_count": 3, "primary_count_field": "primary", "secondary_count_field": "secondary",
            "pregnant_woman_unit": Decimal("7000"), "pregnant_woman_cap_count": 1, "pregnancy_field": "pregnant",
        }
        breakdown = PctPaymentCalculationRule.household_breakdown(beneficiary, params, reference_date=date(2026, 5, 31))
        self.assertEqual((breakdown["pregnant_count_raw"], breakdown["pregnant_count"]), (2, 1))
        self.assertEqual((breakdown["young_child_count"], breakdown["pregnant_amount"]), (3, 7000))
        self.assertEqual(breakdown["capped_total"], 42000)


class PaidChildCountsTest(TestCase):
    PARAMS = {
        "young_child_unit": Decimal("7000"), "young_child_cap_count": 3,
        "primary_unit": Decimal("5000"), "primary_cap_count": 3,
        "secondary_unit": Decimal("8000"), "secondary_cap_count": 3,
        "children_cap_count": 3,
    }

    def paid(self, young, primary, secondary, **override):
        counts = PctPaymentCalculationRule.paid_child_counts(young, primary, secondary, {**self.PARAMS, **override})
        return counts["secondary"], counts["young_child"], counts["primary"]

    def test_three_or_fewer_children_are_all_paid(self):
        self.assertEqual(self.paid(1, 1, 1), (1, 1, 1))
        self.assertEqual(self.paid(0, 2, 0), (0, 0, 2))

    def test_highest_rate_first_when_more_than_three(self):
        self.assertEqual(self.paid(2, 2, 1), (1, 2, 0))
        self.assertEqual(self.paid(1, 3, 2), (2, 1, 0))
        self.assertEqual(self.paid(4, 0, 0), (0, 3, 0))
        self.assertEqual(self.paid(0, 5, 0), (0, 0, 3))

    def test_order_follows_the_configured_rates(self):
        self.assertEqual(self.paid(2, 2, 2, primary_unit=Decimal("9000")), (1, 0, 2))

    def test_total_cap_is_configurable(self):
        self.assertEqual(self.paid(2, 2, 2, children_cap_count=4), (2, 2, 0))


class MemberValueShapesTest(TestCase):
    def _individual(self, json_ext):
        return SimpleNamespace(dob=None, json_ext=json_ext, is_deleted=False)

    def test_reads_flat_and_nested_raw(self):
        flat = self._individual({"raw": {"primary": "2"}})
        nested = self._individual({"json_ext": {"raw": {"primary": "3"}}})
        both = self._individual({"raw": {"primary": "4"}, "json_ext": {"raw": {"primary": "9"}}})
        get = PctPaymentCalculationRule.member_value
        self.assertEqual(get(flat, "primary"), "2")
        self.assertEqual(get(nested, "primary"), "3")
        self.assertEqual(get(both, "primary"), "4")
        self.assertIsNone(get(flat, "secondary"))
