import importlib
import inspect

from django.apps import AppConfig

from calculation.apps import CALCULATION_RULES


MODULE_NAME = "calcrule_pct_payment"
DEFAULT_CFG = {}


def read_all_calculation_rules():
    for _, cls in inspect.getmembers(
        importlib.import_module("calcrule_pct_payment.calculation_rule"),
        inspect.isclass,
    ):
        if cls.__module__ == 'calcrule_pct_payment.calculation_rule':
            CALCULATION_RULES.append(cls)
            cls.ready()


class CalcrulePctPaymentConfig(AppConfig):
    name = MODULE_NAME

    def ready(self):
        from core.models import ModuleConfiguration

        ModuleConfiguration.get_or_default(MODULE_NAME, DEFAULT_CFG)
        read_all_calculation_rules()
