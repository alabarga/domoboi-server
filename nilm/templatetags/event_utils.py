from django import template

register = template.Library()

@register.filter
def get_event_icon(event_type):
    """Material Symbols Rounded icon name for an appliance/event type"""
    icon_mapping = {
        'FRIDGE': 'kitchen',
        'OVEN': 'oven_gen',
        'KETTLE': 'kettle',
        'MICROWAVE': 'microwave',
        'WASHING MACHINE': 'local_laundry_service',
        'IRON': 'iron',
        'LIGHTS': 'lightbulb',
        'TV': 'tv',
        'HEATER': 'heat',
        'COOKTOP': 'cooking',
        'WATER_HEATER': 'water_heater',
    }
    return icon_mapping.get(event_type, 'electrical_services')

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