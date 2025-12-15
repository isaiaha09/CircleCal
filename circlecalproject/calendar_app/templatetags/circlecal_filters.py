from django import template

register = template.Library()


@register.filter(name='length_is')
def length_is(value, arg):
    """Return True if len(value) == int(arg). Works with iterables and strings.

    Usage: {{ mylist|length_is:"1" }}
    """
    try:
        target = int(arg)
    except Exception:
        return False
    try:
        return len(value) == target
    except Exception:
        return False
