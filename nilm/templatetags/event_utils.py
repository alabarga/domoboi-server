from django import template

register = template.Library()

@register.filter
def get_event_icon(event_type):
    """Get the icon class for an event type"""
    icon_mapping = {
        'FRIDGE': 'ri-fridge-line',
        'OVEN': 'ri-exchange-funds-line',
        'KETTLE': 'ri-cup-line',
        'MICROWAVE': 'ri-bowl-line',
        'WASHING MACHINE': 'ri-camera-2-line',
        'IRON': 'ri-t-shirt-air-line',
        'LIGHTS': 'ri-lightbulb-flash-line',
        'TV': 'ri-tv-2-line',
    }
    return icon_mapping.get(event_type, 'ri-question-line')

@register.filter
def get_event_color(event_class):
    """Get the color for an event class"""
    color_mapping = {
        'NORMAL': '#28a745',
        'UNEXPECTED': '#6fdc8c',
        'ANORMAL': '#ffe066',
        'ALERT': '#fd7e14',
    }
    return color_mapping.get(event_class, '#adb5bd')

@register.filter
def get_event_badge_class(event_class):
    """Get the badge class for an event class"""
    badge_mapping = {
        'NORMAL': 'success',
        'UNEXPECTED': 'success-subtle',
        'ANORMAL': 'warning',
        'ALERT': 'orange',
    }
    return badge_mapping.get(event_class, 'secondary') 