from django import template
from django.forms import BoundField

register = template.Library()


@register.filter
def add_class(field: BoundField, css_class: str) -> str:
    """Renders the widget of a form field with an additional CSS class, e.g. Bulma's `input`."""
    classes = f"{field.field.widget.attrs.get('class', '')} {css_class}".strip()
    return field.as_widget(attrs={"class": classes})
