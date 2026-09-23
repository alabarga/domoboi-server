from django.contrib import admin
from unfold.admin import ModelAdmin, StackedInline
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
class UserProfileAdmin(ModelAdmin):
    list_display = ['user', 'get_locations_count']
    list_filter = ['user__is_active']
    search_fields = ['user__username', 'user__email']
    filter_horizontal = ['locations']
    
    def get_locations_count(self, obj):
        return obj.locations.count()
    get_locations_count.short_description = 'Assigned Locations'

@admin.register(Measurement)
class MeasurementAdmin(ModelAdmin):
    list_display = ['device', 'get_location', 'timestamp', 'value']
    list_filter = ['device__location', 'timestamp']
    search_fields = ['device__device_id', 'device__location__description']
    ordering = ['-timestamp']
    raw_id_fields = ['device']
    date_hierarchy = 'timestamp'
    
    def get_location(self, obj):
        return obj.location.description if obj.location else "-"
    get_location.short_description = 'Ubicación'

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
    list_display = ['device_id', 'model', 'device_type', 'location', 'get_is_active', 'get_last_active']
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
class CommentAdmin(ModelAdmin):
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


class UserProfileInline(StackedInline):
    model = UserProfile
    can_delete = False
    extra = 0
    verbose_name_plural = 'Ubicaciones asignadas'
    fields = ['locations']
    filter_horizontal = ['locations']


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    inlines = [UserProfileInline]


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


# Custom Admin Site with Dashboard Context
from unfold.sites import UnfoldAdminSite
from django.db.models import Exists, OuterRef
import json

class CustomAdminSite(UnfoldAdminSite):
    def index(self, request, extra_context=None):
        now = timezone.now()
        # Same window as Device.is_active, so the dashboard and the device list
        # can never disagree about what "active" means.
        active_cutoff = now - Device.ACTIVITY_WINDOW
        seven_days_ago = now - timedelta(days=7)

        active_devices_last_hour = Device.objects.filter(
            measurements__timestamp__gte=active_cutoff
        ).distinct().count()

        assigned_devices = Device.objects.filter(location__isnull=False).count()
        devices_active_7days = Device.objects.filter(
            measurements__timestamp__gte=seven_days_ago
        ).distinct().count()

        # Annotate assigned devices with recent-activity flag for the map
        has_recent_meas = Exists(
            Measurement.objects.filter(device=OuterRef('pk'), timestamp__gte=active_cutoff)
        )
        assigned_device_list = list(
            Device.objects.filter(location__isnull=False)
            .select_related('location')
            .annotate(has_recent=has_recent_meas)
        )

        # Device map: coordinates come from Location.location (PlainLocationField "lat,lon")
        device_map_data = []
        for d in assigned_device_list:
            if not d.location.location:
                continue
            try:
                lat, lon = (float(x.strip()) for x in d.location.location.split(','))
            except ValueError:
                continue
            last = d.last_active
            device_map_data.append({
                'device_id': d.device_id,
                'model': d.model,
                'device_type': d.device_type,
                'location': d.location.description,
                'lat': lat,
                'lon': lon,
                'is_active': d.has_recent,
                'last_active': last.strftime('%Y-%m-%d %H:%M') if last else 'N/A',
            })
        device_map_json = json.dumps(device_map_data)

        extra_context = extra_context or {}
        extra_context.update({
            'assigned_devices': assigned_devices,
            'active_devices_last_hour': active_devices_last_hour,
            'devices_active_7days': devices_active_7days,
            'device_map_json': device_map_json,
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