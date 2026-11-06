import logging
from datetime import date
from decimal import Decimal

from django.db import transaction

from calcrule_pct_payment.config import (
    CLASS_RULE_PARAM_VALIDATION,
    DESCRIPTION_CONTRIBUTION_VALUATION,
    FROM_TO,
)
from calcrule_social_protection.converters import (
    GroupToBenefitConverter,
    GroupToBillConverter,
    GroupToBillItemConverter,
)
from contribution_plan.models import PaymentPlan
from core import datetime
from core.abs_calculation_rule import AbsStrategy
from core.models import User
from invoice.models import Bill
from invoice.services import BillService
from payroll.services import BenefitConsumptionService, PayrollService
from social_protection.models import BeneficiaryStatus, GroupBeneficiary

logger = logging.getLogger(__name__)


class PctPaymentCalculationRule(AbsStrategy):
    version = 1
    uuid = "7ca95e43-e4bc-4d2f-bf99-804bc57d6c4f"
    calculation_rule_name = "Calculation rule: pct household payment"
    description = DESCRIPTION_CONTRIBUTION_VALUATION
    impacted_class_parameter = CLASS_RULE_PARAM_VALIDATION
    date_valid_from = datetime.datetime(2000, 1, 1)
    date_valid_to = None
    status = "active"
    from_to = FROM_TO
    type = "social_protection"
    sub_type = "benefit_plan"
    CLASS_NAME_CHECK = ["PaymentPlan"]

    @classmethod
    def run_calculation_rules(cls, sender, instance, user, context, **kwargs):
        if isinstance(instance, PaymentPlan):
            return cls.calculate_if_active_for_object(instance, **kwargs)
        return False

    @classmethod
    def calculate_if_active_for_object(cls, payment_plan, **kwargs):
        if cls.active_for_object(payment_plan):
            return cls.calculate(payment_plan, **kwargs)
        return False

    @classmethod
    def active_for_object(cls, payment_plan, **kwargs):
        return cls.check_calculation(payment_plan)

    @classmethod
    def get_linked_class(cls, sender, class_name, **kwargs):
        list_class = super().get_linked_class(sender, class_name, **kwargs)
        if class_name == "PaymentPlan":
            list_class.append("Calculation")
        if class_name == "BenefitPlan":
            list_class.append("PaymentPlan")
        if class_name == "GroupBeneficiary":
            list_class.append("BenefitPlan")
        return list_class

    @classmethod
    def get_parameters(cls, sender, class_name, instance, **kwargs):
        rule_details = cls.get_rule_details(sender=sender, class_name=class_name)
        if not rule_details:
            return []
        if instance.__class__.__name__ in cls.CLASS_NAME_CHECK and cls.check_calculation(instance):
            return rule_details["parameters"] if "parameters" in rule_details else []
        if instance.__class__.__name__ == "ABCMeta" and cls.uuid == str(instance.uuid):
            return rule_details["parameters"] if "parameters" in rule_details else []
        return []

    @classmethod
    def run_convert(cls, payment_plan, **kwargs):
        return cls.convert(payment_plan=payment_plan, **kwargs)

    @classmethod
    def check_calculation(cls, payment_plan, **kwargs):
        return str(cls.uuid) == str(payment_plan.calculation)

    @classmethod
    def calculate(cls, payment_plan, **kwargs):
        payroll = kwargs.get("payroll")
        beneficiaries = kwargs.get("beneficiaries_queryset")
        if beneficiaries is None:
            beneficiaries = GroupBeneficiary.objects.filter(
                benefit_plan=payment_plan.benefit_plan,
                status=BeneficiaryStatus.ACTIVE,
                is_deleted=False,
            )

        params = cls._get_rule_params(payment_plan)
        user_id, start_date, end_date, payment_cycle = cls.get_payment_cycle_parameters(**kwargs)
        user = User.objects.filter(id=user_id).first()
        reference_date = end_date or start_date or date.today()

        beneficiaries = beneficiaries.select_related("group").prefetch_related("group__groupindividuals__individual")
        for beneficiary in beneficiaries:
            breakdown = cls._calculate_household_breakdown(
                beneficiary,
                params,
                reference_date=reference_date,
            )
            cls.run_convert(
                payment_plan,
                group=beneficiary,
                amount=Decimal(str(breakdown["capped_total"])),
                user=user,
                end_date=end_date,
                payment_cycle=payment_cycle,
                payroll=payroll,
                breakdown=breakdown,
            )
        return "Calculation and transformation into bills completed successfully."

    @classmethod
    def convert(cls, payment_plan, **kwargs):
        beneficiary = kwargs.get("group")
        amount = kwargs.get("amount")
        payroll = kwargs.get("payroll")
        end_date = kwargs.get("end_date")
        payment_cycle = kwargs.get("payment_cycle")
        user = kwargs.get("user")
        breakdown = kwargs.get("breakdown", {})

        convert_results = cls._convert_entity_to_bill(
            payment_plan=payment_plan,
            beneficiary=beneficiary,
            amount=amount,
            end_date=end_date,
            payment_cycle=payment_cycle,
        )
        convert_results["user"] = user

        convert_results_benefit = cls._convert_entity_to_benefit(
            payment_plan=payment_plan,
            beneficiary=beneficiary,
            amount=amount,
            payment_cycle=payment_cycle,
            breakdown=breakdown,
        )

        cls.create_and_save_business_entities(
            convert_results=convert_results,
            convert_results_benefit=convert_results_benefit,
            payroll_id=payroll.id if payroll else None,
            user=user,
        )
        return True

    @classmethod
    def get_payment_cycle_parameters(cls, **kwargs):
        return (
            kwargs.get("user_id"),
            kwargs.get("start_date"),
            kwargs.get("end_date"),
            kwargs.get("payment_cycle"),
        )

    @classmethod
    def _get_rule_params(cls, payment_plan):
        params = (payment_plan.json_ext or {}).get("calculation_rule", {})
        return {
            "base_amount": cls._as_decimal(params.get("base_amount", "14000")),
            "disability_top_up": cls._as_decimal(params.get("disability_top_up", "5000")),
            "young_child_unit": cls._as_decimal(params.get("young_child_unit", "7000")),
            "young_child_age_min": int(params.get("young_child_age_min", 0)),
            "young_child_age_max": int(params.get("young_child_age_max", 5)),
            "young_child_cap_count": int(params.get("young_child_cap_count", 3)),
            "primary_unit": cls._as_decimal(params.get("primary_unit", "5000")),
            "primary_cap_count": int(params.get("primary_cap_count", 3)),
            "secondary_unit": cls._as_decimal(params.get("secondary_unit", "8000")),
            "secondary_cap_count": int(params.get("secondary_cap_count", 3)),
            "household_cap_amount": cls._as_decimal(params.get("household_cap_amount", "50000")),
            "primary_count_field": params.get("primary_count_field", "primary"),
            "secondary_count_field": params.get("secondary_count_field", "secondary"),
        }

    @classmethod
    def _calculate_household_breakdown(cls, beneficiary, params, reference_date=None):
        members = [
            gi.individual
            for gi in beneficiary.group.groupindividuals.all()
            if not gi.is_deleted and gi.individual and not gi.individual.is_deleted
        ]

        young_child_count_raw = sum(
            1
            for individual in members
            if cls._is_age_in_range(
                individual.dob,
                params["young_child_age_min"],
                params["young_child_age_max"],
                reference_date=reference_date,
            )
        )
        young_child_count = min(young_child_count_raw, params["young_child_cap_count"])

        primary_count_raw = cls._resolve_household_count(members, params["primary_count_field"])
        primary_count = min(primary_count_raw, params["primary_cap_count"])

        secondary_count_raw = cls._resolve_household_count(members, params["secondary_count_field"])
        secondary_count = min(secondary_count_raw, params["secondary_cap_count"])

        has_disability = any(cls._member_has_disability(individual) for individual in members)

        disability_amount = params["disability_top_up"] if has_disability else Decimal("0")
        young_child_amount = params["young_child_unit"] * Decimal(young_child_count)
        primary_amount = params["primary_unit"] * Decimal(primary_count)
        secondary_amount = params["secondary_unit"] * Decimal(secondary_count)
        raw_total = (
            params["base_amount"]
            + disability_amount
            + young_child_amount
            + primary_amount
            + secondary_amount
        )
        capped_total = min(raw_total, params["household_cap_amount"])

        return {
            "base_amount": cls._to_json_number(params["base_amount"]),
            "has_disability": has_disability,
            "disability_amount": cls._to_json_number(disability_amount),
            "young_child_count_raw": young_child_count_raw,
            "young_child_count": young_child_count,
            "young_child_amount": cls._to_json_number(young_child_amount),
            "primary_count_raw": primary_count_raw,
            "primary_count": primary_count,
            "primary_amount": cls._to_json_number(primary_amount),
            "secondary_count_raw": secondary_count_raw,
            "secondary_count": secondary_count,
            "secondary_amount": cls._to_json_number(secondary_amount),
            "raw_total": cls._to_json_number(raw_total),
            "household_cap_amount": cls._to_json_number(params["household_cap_amount"]),
            "capped_total": cls._to_json_number(capped_total),
        }

    @classmethod
    def _resolve_household_count(cls, members, field_name):
        values = [cls._as_int(cls._get_member_value(individual, field_name)) for individual in members]
        values = [value for value in values if value is not None and value >= 0]
        return max(values) if values else 0

    @classmethod
    def _member_has_disability(cls, individual):
        disability_fields = [
            "wg_visual",
            "wg_hearing",
            "wg_mobility",
            "wg_memory",
            "wg_selfcare",
            "wg_communication",
            "disability_visual",
            "disability_hearing",
            "disability_mobility",
            "disability_memory",
            "disability_selfcare",
            "disability_mute",
            "disability_albinism",
        ]
        return any(cls._is_truthy(cls._get_member_value(individual, field_name)) for field_name in disability_fields)

    @classmethod
    def _get_member_value(cls, individual, field_name):
        json_ext = individual.json_ext or {}
        nested_json_ext = json_ext.get("json_ext", {}) if isinstance(json_ext.get("json_ext", {}), dict) else {}
        for source in (json_ext, json_ext.get("raw"), nested_json_ext, nested_json_ext.get("raw")):
            if isinstance(source, dict) and field_name in source:
                return source.get(field_name)
        return None

    @classmethod
    def _is_age_in_range(cls, dob, age_min, age_max, reference_date=None):
        age = cls._age_years(dob, reference_date=reference_date)
        return age is not None and age_min <= age <= age_max

    @classmethod
    def _age_years(cls, dob, reference_date=None):
        if not dob:
            return None
        if isinstance(dob, date):
            dob_date = dob
        else:
            try:
                dob_date = date.fromisoformat(str(dob)[:10])
            except ValueError:
                return None
        effective_date = reference_date or date.today()
        return effective_date.year - dob_date.year - (
            (effective_date.month, effective_date.day) < (dob_date.month, dob_date.day)
        )

    @classmethod
    def _is_truthy(cls, value):
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        return text in {"1", "true", "t", "yes", "y"}

    @classmethod
    def _as_int(cls, value):
        if value in (None, ""):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @classmethod
    def _as_decimal(cls, value):
        return Decimal(str(value))

    @classmethod
    def _to_json_number(cls, value):
        return int(value) if value == value.to_integral_value() else float(value)

    @classmethod
    def _convert_entity_to_bill(cls, payment_plan, beneficiary, amount, end_date, payment_cycle):
        bill = GroupToBillConverter.to_bill_obj(payment_plan, beneficiary, amount, end_date, payment_cycle)
        bill_line_items = [GroupToBillItemConverter.to_bill_item_obj(payment_plan, beneficiary, amount)]
        return {
            "bill_data": bill,
            "bill_data_line": bill_line_items,
            "type_conversion": "beneficiary - bill",
        }

    @classmethod
    def _convert_entity_to_benefit(cls, payment_plan, beneficiary, amount, payment_cycle, breakdown):
        benefit = GroupToBenefitConverter.to_benefit_obj(beneficiary, amount, payment_plan, payment_cycle)
        benefit["json_ext"] = {
            "pct_breakdown": breakdown,
            "calculation_rule_uuid": cls.uuid,
            "benefit_plan_code": payment_plan.benefit_plan.code,
            "beneficiary_group_id": str(beneficiary.group_id) if beneficiary.group_id else None,
        }
        return {
            "benefit_data": benefit,
            "type_conversion": "beneficiary - benefit",
        }

    @classmethod
    @transaction.atomic
    def create_and_save_business_entities(cls, convert_results, convert_results_benefit, payroll_id, user, bill_status=None):
        if bill_status is not None:
            convert_results["bill_data"]["status"] = bill_status
        result_bill_creation = BillService.bill_create(convert_results=convert_results)
        if not result_bill_creation["success"]:
            raise ValueError(
                f"PCT bill creation failed: {result_bill_creation.get('detail') or result_bill_creation.get('message')}"
            )

        bill_id = result_bill_creation["data"]["id"]
        benefit_service = BenefitConsumptionService(user)
        benefit_result = benefit_service.create(convert_results_benefit["benefit_data"])
        if not benefit_result["success"]:
            raise ValueError(
                f"PCT benefit creation failed: {benefit_result.get('detail') or benefit_result.get('message')}"
            )

        bill_queryset = Bill.objects.filter(id__in=[bill_id])
        benefit_id = benefit_result["data"]["id"]
        benefit_service.create_or_update_benefit_attachment(bill_queryset, benefit_id)
        if payroll_id:
            payroll_service = PayrollService(user=user)
            payroll_service.attach_benefit_to_payroll(payroll_id, benefit_id)
        return result_bill_creation
