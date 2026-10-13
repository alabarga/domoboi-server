from django.contrib import admin

from .models import EventLabel


@admin.register(EventLabel)
class EventLabelAdmin(admin.ModelAdmin):
    list_display = ('measurement', 'appliance_type', 'labeled_by', 'updated_at')
    list_filter = ('appliance_type',)
    autocomplete_fields = ()
    search_fields = ('measurement__device__device_id',)
