from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from location_field.models.plain import PlainLocationField
import random

class Location(models.Model):
    """Model for storing location information"""
    description = models.CharField(max_length=200)
    address = models.CharField(max_length=255)
    location = PlainLocationField(based_fields=['address'], zoom=7, default='41.3874,2.1686')
    
    def __str__(self):
        return self.description
    
    class Meta:
        ordering = ['description']

    @property
    def score(self):
        # Deterministic score based on self.id as a seed to ensure constancy
        import random
        random.seed(self.id)
        return random.randint(35, 100)

    @property
    def score_class(self):
        s = self.score
        if s >= 80:
            return 'score-green'
        elif s >= 50:
            return 'score-orange'
        else:
            return 'score-red'

    @property
    def alerts_last_24h(self):
        from django.utils import timezone
        return self.events.filter(class_name='ALERT', start_time__gte=timezone.now() - timezone.timedelta(days=1)).count()

    @property
    def alerts_last_week(self):
        from django.utils import timezone
        return self.events.filter(class_name='ALERT', start_time__gte=timezone.now() - timezone.timedelta(days=7)).count()

class Person(models.Model):
    """Model for storing person information"""
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Other'),
    ]
    
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    age = models.PositiveIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(150)]
    )
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='persons')
    
    def __str__(self):
        return f"{self.name} ({self.location.description})"
    
    class Meta:
        ordering = ['name']

class UserProfile(models.Model):
    """Model for extending user with location access"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    locations = models.ManyToManyField(Location, related_name='assigned_users')
    
    def __str__(self):
        return f"Profile for {self.user.username}"
    
    def has_access_to_location(self, location):
        """Check if user has access to a specific location"""
        return self.user.is_superuser or location in self.locations.all()


class Device(models.Model):
    """Model for storing device information"""
    DEVICE_TYPES = [
        ('DOMOBOI', 'Domoboi Edge (ATM90E36)'),
        ('TUYA',    'Tuya Cloud Device'),
    ]
    device_id = models.CharField(max_length=100, unique=True)
    model = models.CharField(max_length=100)
    device_type = models.CharField(max_length=10, choices=DEVICE_TYPES, default='DOMOBOI')
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL, related_name='devices')

    @property
    def last_active(self):
        last_meas = self.measurements.order_by('-timestamp').first()
        return last_meas.timestamp if (last_meas and last_meas.timestamp) else None
    
    @property
    def is_active(self):
        """Returns True if the device has sent a measurement in the last 60 minutes"""
        cutoff = timezone.now() - timezone.timedelta(minutes=60)
        return self.measurements.filter(timestamp__gte=cutoff).exists()
    
    def __str__(self):
        loc = self.location.description if self.location else "Unassigned"
        return f"{self.model} ({self.device_id}) - {loc}"
    
    class Meta:
        ordering = ['model', 'device_id']

class Measurement(models.Model):
    """Model for storing high-resolution electrical reading segments during appliance runs"""
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='measurements', null=True, blank=True)
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)

    # JSON array of raw numeric samples (Amperes or Watts) — populated by DOMOBOI edge devices
    readings = models.JSONField(default=list)

    # Statistical summary of the readings window — populated by domoboi-edge: {avg, min, max, std}
    features = models.JSONField(null=True, blank=True, default=None)

    # Structured snapshot from calibrated meters (Tuya) — {power_w, voltage_v, current_a, energy_kwh, ...}
    telemetry = models.JSONField(null=True, blank=True, default=None)

    # Legacy fields (automatically populated for compatibility with existing admin/dashboards)
    timestamp = models.DateTimeField(null=True, blank=True)
    value = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)

    @property
    def location(self):
        return self.device.location if self.device else None

    def save(self, *args, **kwargs):
        if not self.timestamp:
            self.timestamp = self.start_time
        if self.readings and len(self.readings) > 0 and (self.value is None or self.value == 0.0):
            # DOMOBOI path: auto-compute value from readings mean
            self.value = sum(self.readings) / len(self.readings)
        elif not self.readings and self.telemetry and self.telemetry.get('power_w'):
            # Tuya path: back-fill readings from power_w for backward compat
            power = float(self.telemetry['power_w'])
            self.readings = [power]
            if not self.value:
                self.value = power
        super().save(*args, **kwargs)

    def __str__(self):
        loc = self.location
        loc_desc = loc.description if loc else "Unknown"
        if self.start_time and self.end_time:
            duration = int((self.end_time - self.start_time).total_seconds())
            t_str = self.timestamp.strftime('%Y-%m-%d %H:%M:%S') if self.timestamp else 'N/A'
            return f"{loc_desc} - Segment {t_str} ({duration}s, {len(self.readings)} samples)"
        t_str = self.timestamp.strftime('%Y-%m-%d %H:%M:%S') if self.timestamp else 'N/A'
        return f"{loc_desc} - Segment {t_str}"
    
    class Meta:
        ordering = ['-timestamp']


class Event(models.Model):
    """Model for storing detected events"""
    APPLIANCE_TYPES = [
        ('FRIDGE', _('Fridge')),
        ('OVEN', _('Oven')),
        ('KETTLE', _('Kettle')),
        ('MICROWAVE', _('Microwave')),
        ('WASHING MACHINE', _('Washing Machine')),
        ('IRON', _('Iron')),
        ('LIGHTS', _('Lights')),
        ('TV', _('TV')),
    ]
    EVENT_CLASSES = [
        ('NORMAL', _('Normal')),
        ('UNEXPECTED', _('Unexpected')),
        ('ANORMAL', _('Anormal')),
        ('ALERT', _('Alert')),
    ]
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='events')
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    type = models.CharField(max_length=20, choices=APPLIANCE_TYPES)
    class_name = models.CharField(max_length=20, choices=EVENT_CLASSES)
    description = models.TextField(blank=True)
    
    def __str__(self):
        return f"{self.location.description} - {self.type}: {self.class_name}"

    @property
    def duration_minutes(self):
        diff = self.end_time - self.start_time
        return int(diff.total_seconds() / 60)
    
    class Meta:
        ordering = ['-start_time']
        
class Comment(models.Model):
    """Model for storing user comments about persons"""
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='comments')
    timestamp = models.DateTimeField(auto_now_add=True)
    message = models.TextField()
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    
    def __str__(self):
        return f"Comment by {self.author.username} on {self.person.name}"
    
    class Meta:
        ordering = ['-timestamp']


# Signals to automatically create/save UserProfile when a User is created/saved
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    UserProfile.objects.get_or_create(user=instance)

