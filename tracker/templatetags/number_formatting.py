from django import template

register = template.Library()

@register.filter
def dollar_format(value):
    """Format a number as a dollar amount."""
    if value is None:
        return "$0.00"
    try:
        value = float(value)
        if value < 0:
            return "-${:,.2f}".format(abs(value))
        return "${:,.2f}".format(value)
    except (ValueError, TypeError):
        return "$0.00"