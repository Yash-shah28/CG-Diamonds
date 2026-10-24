"""Website-specific outgoing price adjustments. Original workbook data is untouched."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


WEBSITES = ({'id': 'drc', 'name': 'DRC'},)


class RateConfigError(ValueError):
    pass


def rate_config(settings, website='drc'):
    config = settings.get(website, {})
    mode = config.get('mode', 'discount')
    raw = config.get('percent', '0')
    try:
        if isinstance(raw, bool): raise ValueError()
        percent = Decimal(str(raw))
        if (mode not in ('discount', 'increase') or not percent.is_finite() or percent < 0 or
                percent > 100 or (mode == 'discount' and percent >= 100)):
            raise ValueError()
    except (ValueError, InvalidOperation):
        raise RateConfigError('Invalid website rate adjustment. Discounts must be below 100%; increases may be up to 100%. Correct it in Change Rates.') from None
    return mode, percent


def apply_website_rate(payload, settings, website='drc'):
    mode, percent = rate_config(settings, website)
    adjusted = deepcopy(payload)
    if not percent:
        return adjusted
    factor = Decimal(1) + (percent if mode == 'increase' else -percent) / Decimal(100)
    for number, record in enumerate(adjusted['diamonds'], 1):
        try:
            original = record['PricePerCarat']
            if isinstance(original, bool): raise ValueError()
            price = Decimal(str(original))
            if not price.is_finite() or price <= 0: raise ValueError()
            result = (price * factor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if result <= 0: raise ValueError()
        except (KeyError, ValueError, InvalidOperation):
            raise ValueError(f'Record {number}: Price Per Carat cannot be adjusted to a valid positive price.') from None
        record['PricePerCarat'] = format(result, 'f') if isinstance(original, str) else float(result)
    return adjusted
