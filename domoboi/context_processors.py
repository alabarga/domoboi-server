from django.conf import settings


def google_maps_api_key(request):
    return {'GOOGLE_MAPS_API_KEY': settings.GOOGLE_MAPS_API_KEY}


def webpush_settings(request):
    return {'WEBPUSH_SETTINGS': getattr(settings, 'WEBPUSH_SETTINGS', {})}
