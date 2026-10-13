from django import forms

from .models import EventLabel


class EventLabelForm(forms.ModelForm):
    class Meta:
        model = EventLabel
        fields = ['appliance_type', 'notes']
