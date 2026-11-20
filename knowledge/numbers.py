import re
from decimal import Decimal

from django.core.exceptions import ValidationError


def parse_number(value):
    text = str(value).strip().replace("−", "-")
    if not re.fullmatch(r"[+-]?[0-9]{1,16}(?:[.,][0-9]{1,8})?", text):
        raise ValidationError("Введіть число: до 16 цифр перед роздільником і до 8 після. Кома або крапка — десятковий роздільник; без тисяч, дробів і формул.")
    return Decimal(text.replace(",", "."))
