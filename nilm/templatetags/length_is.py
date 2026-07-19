from django import template
register = template.Library()

@register.filter
def length_is(value, arg):
    """Return True if the length of value is equal to arg."""
    try:
        return len(value) == int(arg)
    except (TypeError, ValueError):
        return False 