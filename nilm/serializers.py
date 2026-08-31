from rest_framework import serializers
from .models import Event, Measurement


class EventIngestionSerializer(serializers.Serializer):
    device_id = serializers.CharField(
        help_text="Unique device identifier (must be registered in the system)"
    )
    start_time = serializers.DateTimeField(
        help_text="Event start time (ISO-8601)"
    )
    end_time = serializers.DateTimeField(
        help_text="Event end time (ISO-8601)"
    )
    type = serializers.ChoiceField(
        choices=[c[0] for c in Event.APPLIANCE_TYPES],
        help_text="Detected appliance type"
    )
    class_name = serializers.ChoiceField(
        choices=[c[0] for c in Event.EVENT_CLASSES],
        default='NORMAL',
        help_text="Event classification"
    )
    description = serializers.CharField(
        required=False, default='', allow_blank=True,
        help_text="Optional free-text description"
    )


class EventIngestionResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    event_id = serializers.IntegerField()


class MeasurementIngestionSerializer(serializers.Serializer):
    device_id = serializers.CharField(
        help_text="Unique device identifier (must be registered in the system)"
    )
    start_time = serializers.DateTimeField(
        help_text="Window start time (ISO-8601)"
    )
    end_time = serializers.DateTimeField(
        help_text="Window end time (ISO-8601)"
    )
    readings = serializers.ListField(
        child=serializers.FloatField(),
        required=False, default=list,
        help_text=(
            "Raw numeric samples (Amperes or Watts) recorded during the window "
            "(DOMOBOI edge devices). For Tuya devices this is auto-filled from telemetry.power_w."
        )
    )
    value = serializers.FloatField(
        required=False, allow_null=True, default=None,
        help_text=(
            "Primary scalar value (net step-change in Watts for DOMOBOI, "
            "current power in Watts for Tuya). Auto-computed as mean(readings) if omitted."
        )
    )
    features = serializers.DictField(
        required=False, allow_null=True, default=None,
        help_text=(
            "Statistical summary of the readings window from domoboi-edge: "
            "{avg, min, max, std}"
        )
    )
    telemetry = serializers.DictField(
        required=False, allow_null=True, default=None,
        help_text=(
            "Structured snapshot from Tuya cloud devices: "
            "{power_w, voltage_v, current_a, energy_kwh, power_factor, frequency_hz, ...}"
        )
    )


class MeasurementIngestionResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    measurement_id = serializers.IntegerField()


class DeviceCheckSerializer(serializers.Serializer):
    device_id = serializers.CharField(
        help_text="Device identifier to look up"
    )


class DeviceCheckResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['ok', 'NOK'])
    device_id = serializers.CharField(required=False)
    model = serializers.CharField(required=False)
    location = serializers.CharField(required=False)
    message = serializers.CharField(required=False)
