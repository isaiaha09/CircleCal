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


@register.filter(name='role_label')
def role_label(value):
    """Display label for membership roles.

    Keeps stored role values intact (e.g. 'admin' remains 'admin'); this is UI-only.
    """
    try:
        r = (str(value) if value is not None else '').strip().lower()
    except Exception:
        r = ''

    if r == 'admin':
        return 'GM'
    if not r:
        return 'Staff'
    return r[:1].upper() + r[1:]
