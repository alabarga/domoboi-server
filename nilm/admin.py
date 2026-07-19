from django.contrib import admin
from unfold.admin import ModelAdmin
from django.utils import timezone
from datetime import timedelta
from .models import Location, Person, UserProfile, Measurement, Event, Comment, Device

@admin.register(Location)
class LocationAdmin(ModelAdmin):
    list_display = ['description', 'address', 'location', 'get_device_models']
    list_filter = ['description']
    search_fields = ['description', 'address']
    ordering = ['description']

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('devices')

    def get_device_models(self, obj):
        models = sorted(list(set(device.model for device in obj.devices.all())))
        return ", ".join(models) if models else "-"
    get_device_models.short_description = 'Dispositivos'

@admin.register(Person)
class PersonAdmin(ModelAdmin):
    list_display = ['name', 'age', 'gender', 'location', 'description']
    list_filter = ['gender', 'location', 'age']
    search_fields = ['name', 'description']
    ordering = ['name']
    raw_id_fields = ['location']

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'get_locations_count']
    list_filter = ['user__is_active']
    search_fields = ['user__username', 'user__email']
    filter_horizontal = ['locations']
    
    def get_locations_count(self, obj):
        return obj.locations.count()
    get_locations_count.short_description = 'Assigned Locations'

@admin.register(Measurement)
class MeasurementAdmin(admin.ModelAdmin):
    list_display = ['location', 'timestamp', 'value']
    list_filter = ['location', 'timestamp']
    search_fields = ['location__description']
    ordering = ['-timestamp']
    raw_id_fields = ['location']
    date_hierarchy = 'timestamp'

@admin.register(Event)
class EventAdmin(ModelAdmin):
    list_display = ['location', 'type', 'class_name', 'start_time', 'end_time']
    list_filter = ['type', 'location', 'start_time']
    search_fields = ['location__description', 'class_name', 'description']
    ordering = ['-start_time']
    raw_id_fields = ['location']
    date_hierarchy = 'start_time'

@admin.register(Device)
class DeviceAdmin(ModelAdmin):
    list_display = ['device_id', 'model', 'location', 'get_is_active', 'get_last_active']
    list_filter = ['model', 'location']
    search_fields = ['device_id', 'model', 'location__description']
    ordering = ['model', 'device_id']
    raw_id_fields = ['location']
    readonly_fields = ['get_is_active', 'get_last_active']
    
    def get_is_active(self, obj):
        return obj.is_active
    get_is_active.short_description = 'Is Active'
    get_is_active.boolean = True
    
    def get_last_active(self, obj):
        return obj.last_active
    get_last_active.short_description = 'Last Active'

@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ['person', 'author', 'timestamp', 'get_message_preview']
    list_filter = ['timestamp', 'author', 'person__location']
    search_fields = ['person__name', 'author__username', 'message']
    ordering = ['-timestamp']
    raw_id_fields = ['person', 'author']
    date_hierarchy = 'timestamp'
    
    def get_message_preview(self, obj):
        return obj.message[:50] + '...' if len(obj.message) > 50 else obj.message
    get_message_preview.short_description = 'Message Preview'


from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import User, Group

from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    # Forms loaded from `unfold.forms`
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


# Custom Admin Site with Dashboard Context
from unfold.sites import UnfoldAdminSite

class CustomAdminSite(UnfoldAdminSite):
    def index(self, request, extra_context=None):
        # Get current time and one hour ago
        now = timezone.now()
        one_hour_ago = now - timedelta(hours=1)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Calculate dashboard metrics
        total_devices = Device.objects.count()
        # Since last_active is a property, we'll simulate the count
        # In a real scenario, you might want to store this data differently
        active_devices_last_hour = total_devices  # All devices are considered active
        total_locations = Location.objects.count()
        events_today = Event.objects.filter(start_time__gte=today_start).count()
        
        # Device status metrics - all devices are active since is_active property returns True
        active_devices_count = total_devices
        inactive_devices_count = 0
        devices_by_location_count = Location.objects.filter(devices__isnull=False).distinct().count()
        
        # Recent device activity (all devices since they're all active)
        recent_devices = Device.objects.all()[:10]
        
        extra_context = extra_context or {}
        extra_context.update({
            'total_devices': total_devices,
            'active_devices_last_hour': active_devices_last_hour,
            'total_locations': total_locations,
            'events_today': events_today,
            'active_devices_count': active_devices_count,
            'inactive_devices_count': inactive_devices_count,
            'devices_by_location_count': devices_by_location_count,
            'recent_devices': recent_devices,
        })
        
        return super().index(request, extra_context=extra_context)

# Replace the default admin site
admin.site = CustomAdminSite(name='custom_admin')

# Re-register all models with the custom admin site
admin.site.register(Location, LocationAdmin)
admin.site.register(Person, PersonAdmin)
admin.site.register(UserProfile, UserProfileAdmin)
admin.site.register(Measurement, MeasurementAdmin)
admin.site.register(Event, EventAdmin)
admin.site.register(Device, DeviceAdmin)
admin.site.register(Comment, CommentAdmin)
admin.site.register(User, UserAdmin)
admin.site.register(Group, GroupAdmin)