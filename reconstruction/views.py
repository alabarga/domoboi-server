import datetime
import json

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from nilm.models import Device, Measurement

from .forms import EventLabelForm
from .models import EventLabel
from .signal import (
    build_daily_reconstruction,
    build_month_calendar,
    build_tuya_energy,
    device_event_averages,
    _tuya_power,
)

# Los Tuya hacen polling cada ~60s (ver TUYA_POLL_INTERVAL): un día entero
# puede acumular >1000 muestras. Se limita la lista renderizada para no
# tumbar la página; la reconstrucción/gráfico sí usa el día completo.
MAX_EVENT_ROWS = 500


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Herramienta de análisis interna: solo staff/superusuarios."""

    def test_func(self):
        return self.request.user.is_staff or self.request.user.is_superuser


class DeviceListView(StaffRequiredMixin, View):
    template_name = 'reconstruction/device_list.html'

    def get(self, request):
        devices = list(Device.objects.select_related('location').order_by('model', 'device_id'))
        averages, global_avg = device_event_averages(devices)
        for d in devices:
            d.event_stats = averages.get(d.id, {'total': 0, 'active_days': 0, 'avg_per_day': None})
        return render(request, self.template_name, {
            'devices': devices,
            'global_avg_events_per_day': global_avg,
        })


class DeviceJumpView(StaffRequiredMixin, View):
    """Permite ir directamente a un device_id escrito a mano."""

    def get(self, request):
        device_id = request.GET.get('device_id', '').strip()
        if not device_id:
            return redirect('reconstruction:device_list')
        return redirect('reconstruction:device_signal', device_id=device_id)


class DeviceSignalView(StaffRequiredMixin, View):
    template_name = 'reconstruction/device_signal.html'
    partial_template_name = 'reconstruction/partials/day_panel.html'

    def get(self, request, device_id):
        device = get_object_or_404(Device, device_id=device_id)
        # domoboi-server usa USE_TZ=True y TIME_ZONE='Europe/Madrid': "hoy" se
        # calcula en hora de Madrid. La rama USE_TZ=False es solo compatibilidad.
        now = timezone.now()
        today = timezone.localtime(now).date() if settings.USE_TZ else now.date()

        try:
            year = int(request.GET.get('year', today.year))
            month = int(request.GET.get('month', today.month))
        except (TypeError, ValueError):
            year, month = today.year, today.month
        # normaliza mes fuera de rango (navegación prev/next en los bordes del año)
        while month < 1:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1

        day_str = request.GET.get('day')
        selected_day = None
        if day_str:
            try:
                selected_day = datetime.date.fromisoformat(day_str)
            except ValueError:
                selected_day = None

        weeks, total_month_events = build_month_calendar(device, year, month)

        # Si no se pidió un día concreto, abrimos en hoy (si tiene eventos
        # y cae en el mes visible) o si no, en el último día del mes con
        # eventos.
        if selected_day is None:
            candidates = [c['date'] for row in weeks for c in row if c and c['count'] > 0]
            if today.year == year and today.month == month and today in candidates:
                selected_day = today
            elif candidates:
                selected_day = max(candidates)

        points, events = ([], [])
        if selected_day:
            points, events = build_daily_reconstruction(device, selected_day)

        is_tuya = device.device_type == 'TUYA'

        # Los Tuya reportan también energía (kWh) aparte de la potencia:
        # se dibuja como barras, no como parte de la curva de W.
        energy_points, energy_total_kwh, energy_readings = ([], 0, 0)
        if is_tuya and selected_day:
            energy_points, energy_total_kwh, energy_readings = build_tuya_energy(device, selected_day)

        total_events_that_day = len(events)
        events_for_rows = events[:MAX_EVENT_ROWS]

        labels_by_measurement = {}
        if events_for_rows:
            for el in EventLabel.objects.filter(measurement__in=events_for_rows):
                labels_by_measurement[el.measurement_id] = el

        event_rows = []
        prev_power = None
        for m in events_for_rows:
            label = labels_by_measurement.get(m.id)
            row = {
                'measurement': m,
                'label': label,
                'form': EventLabelForm(instance=label or EventLabel(measurement=m)),
            }
            if is_tuya:
                # Muestra periódica de potencia absoluta: no hay ΔP/ON-OFF
                # de un evento discreto, sino el nivel medido en ese instante.
                power = _tuya_power(m)
                row['power'] = power
                row['delta_vs_prev'] = None if prev_power is None else power - prev_power
                prev_power = power
            else:
                row['direction'] = 'ON' if (m.value or 0) >= 0 else 'OFF'
            event_rows.append(row)

        prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        next_month = (year + 1, 1) if month == 12 else (year, month + 1)

        context = {
            'device': device,
            'is_tuya': is_tuya,
            'year': year,
            'month': month,
            # Se formatea en la plantilla con el filtro `date` de Django
            # (traducido a es/ca vía locale/), no con strftime: strftime
            # depende del locale del SO, que el servidor no garantiza.
            'first_of_month': datetime.date(year, month, 1),
            'weeks': weeks,
            'total_month_events': total_month_events,
            'selected_day': selected_day,
            'event_rows': event_rows,
            'total_events_that_day': total_events_that_day,
            'events_truncated': total_events_that_day > MAX_EVENT_ROWS,
            'chart_points_json': json.dumps(points),
            'chart_energy_json': json.dumps(energy_points),
            'energy_readings': energy_readings,
            'energy_total_kwh': energy_total_kwh,
            'prev_month': prev_month,
            'next_month': next_month,
            'today': today,
        }

        if request.headers.get('HX-Request'):
            return render(request, self.partial_template_name, context)
        return render(request, self.template_name, context)


class LabelUpdateView(StaffRequiredMixin, View):
    """Guarda/actualiza/borra la etiqueta manual de un evento (AJAX/HTMX)."""

    def post(self, request, device_id, measurement_id):
        device = get_object_or_404(Device, device_id=device_id)
        measurement = get_object_or_404(Measurement, pk=measurement_id, device=device)

        appliance_type = request.POST.get('appliance_type', '').strip()
        label = EventLabel.objects.filter(measurement=measurement).first()

        if not appliance_type:
            if label:
                label.delete()
            label = None
        else:
            if not label:
                label = EventLabel(measurement=measurement)
            label.appliance_type = appliance_type
            label.labeled_by = request.user
            label.save()

        return render(request, 'reconstruction/partials/label_badge.html', {
            'measurement': measurement,
            'label': label,
            'form': EventLabelForm(instance=label or EventLabel(measurement=measurement)),
            'device': device,
        })
