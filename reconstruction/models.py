from django.conf import settings
from django.db import models

from nilm.models import Event, Measurement


class EventLabel(models.Model):
    """
    Etiqueta manual de electrodoméstico para un evento NILM capturado
    (una ventana de ~3s alrededor de una transición ON/OFF).

    No modifica el modelo `Measurement` original: vive en su propia tabla,
    pensada como herramienta de etiquetado para construir el dataset de
    entrenamiento del clasificador NILM (TFM), evento a evento.
    """

    APPLIANCE_CHOICES = list(Event.APPLIANCE_TYPES) + [
        ('OTHER', 'Otro / sin identificar'),
    ]

    measurement = models.OneToOneField(
        Measurement, on_delete=models.CASCADE, related_name='label'
    )
    appliance_type = models.CharField(max_length=20, choices=APPLIANCE_CHOICES)
    notes = models.CharField(max_length=255, blank=True, default='')
    labeled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Etiqueta de evento'
        verbose_name_plural = 'Etiquetas de eventos'

    def __str__(self):
        return f"Measurement #{self.measurement_id} -> {self.get_appliance_type_display()}"
