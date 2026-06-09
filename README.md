# openIMIS Backend calcrule_pct_payment module

Dedicated openIMIS calculation rule module for TASAF Productive Cash Transfer.

This rule is designed for the household-level PCT formula:

- base direct support
- one-time household disability top-up
- young-child top-up
- primary-school top-up
- secondary-school top-up
- overall household cap

The rule is linked from `PaymentPlan.calculation` and reads its parameters
from `PaymentPlan.json_ext["calculation_rule"]`.
