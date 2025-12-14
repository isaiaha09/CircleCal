from django import template
import os

register = template.Library()

@register.filter
def basename(value):
    """Return the base filename for a path-like string (e.g., avatars/profile_pic.jpg -> profile_pic.jpg)."""
    if not value:
        return ''
    try:
        return os.path.basename(str(value))
    except Exception:
        return value
