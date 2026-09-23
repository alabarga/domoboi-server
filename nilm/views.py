import datetime
import json
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import ListView, DetailView, UpdateView, CreateView, View
from django.views.generic.edit import FormView
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q
from django.core.paginator import Paginator
from django.contrib.auth.models import User
from django.utils import timezone
from .models import Location, Person, Event, Comment, UserProfile, Device
from .forms import CommentForm, LocationUpdateForm, UserProfileUpdateForm, UserLocationAssignmentForm

class LocationListView(LoginRequiredMixin, ListView):
    """View for listing locations accessible to the user"""
    model = Location
    template_name = 'nilm/location_list.html'
    context_object_name = 'locations'
    
    def get_queryset(self):
        if self.request.user.is_superuser:
            return Location.objects.all()
        else:
            # Get locations assigned to the user
            try:
                profile = self.request.user.profile
                return profile.locations.all()
            except UserProfile.DoesNotExist:
                return Location.objects.none()

class LocationMapView(LoginRequiredMixin, ListView):
    """View for displaying locations on a map"""
    model = Location
    template_name = 'nilm/location_map.html'
    context_object_name = 'locations'

    def get_queryset(self):
        if self.request.user.is_superuser:
            return Location.objects.all()
        else:
            try:
                profile = self.request.user.profile
                return profile.locations.all()
            except UserProfile.DoesNotExist:
                return Location.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['location_map_json'] = _build_location_map_json(self.get_queryset())
        return context

class LocationDetailView(LoginRequiredMixin, DetailView):
    """View for displaying location details"""
    model = Location
    template_name = 'nilm/location_detail.html'
    context_object_name = 'location'

    def get_template_names(self):
        # HTMX date-picker swap: return only the events column partial
        if self.request.headers.get('HX-Request'):
            return ['nilm/partials/events_column.html']
        return super().get_template_names()
    
    def get_queryset(self):
        if self.request.user.is_superuser:
            return Location.objects.all()
        else:
            try:
                profile = self.request.user.profile
                return profile.locations.all()
            except UserProfile.DoesNotExist:
                return Location.objects.none()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['persons'] = self.object.persons.all()

        # Date filter — default to today
        date_str = self.request.GET.get('date')
        try:
            selected_date = datetime.date.fromisoformat(date_str) if date_str else timezone.localdate()
        except ValueError:
            selected_date = timezone.localdate()

        events_qs = self.object.events.filter(start_time__date=selected_date).order_by('-start_time')
        if events_qs.exists():
            paginator = Paginator(events_qs, 10)
            context['recent_events'] = paginator.get_page(1)
            context['has_next_events'] = paginator.num_pages > 1
            context['is_placeholder'] = False
        else:
            context['recent_events'] = get_placeholder_events(self.object, selected_date)
            context['has_next_events'] = False
            context['is_placeholder'] = True
        context['selected_date'] = selected_date

        # Most recent previous date that has real events (for cross-date scroll)
        context['prev_date'] = (
            self.object.events
            .filter(start_time__date__lt=selected_date)
            .order_by('-start_time')
            .values_list('start_time__date', flat=True)
            .first()
        )

        # Map data — single marker for this location
        loc = self.object
        map_data = []
        if loc.location:
            try:
                lat, lon = (float(x.strip()) for x in loc.location.split(','))
                map_data = [{'pk': loc.pk, 'description': loc.description, 'lat': lat, 'lon': lon}]
            except ValueError:
                pass
        context['location_map_json'] = json.dumps(map_data)
        return context

class LocationUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """View for updating location information"""
    model = Location
    form_class = LocationUpdateForm
    template_name = 'nilm/location_form.html'
    
    def test_func(self):
        location = self.get_object()
        if self.request.user.is_superuser:
            return True
        try:
            profile = self.request.user.profile
            return location in profile.locations.all()
        except UserProfile.DoesNotExist:
            return False
    
    def get_success_url(self):
        return reverse('nilm:location_detail', kwargs={'pk': self.object.pk})

def service_worker(request):
    """Serve the service worker JS with no-cache headers so updates are always picked up."""
    from django.template.response import TemplateResponse
    response = TemplateResponse(
        request, 'nilm/sw.js',
        content_type='application/javascript; charset=utf-8'
    )
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Service-Worker-Allowed'] = '/nilm/'
    return response


class LocationAssignView(LoginRequiredMixin, View):
    """Assign a location to the currently logged-in user's profile."""
    def get(self, request, location_id):
        location = get_object_or_404(Location, pk=location_id)
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.locations.add(location)
        messages.success(request, f"Se ha asignado la ubicación «{location.description}».")
        return redirect('nilm:location_detail', pk=location_id)


class PersonListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """View for listing all persons (admin only)"""
    model = Person
    template_name = 'nilm/person_list.html'
    context_object_name = 'persons'
    
    def test_func(self):
        return self.request.user.is_superuser

class PersonDetailView(LoginRequiredMixin, DetailView):
    """View for displaying person details"""
    model = Person
    template_name = 'nilm/person_detail.html'
    context_object_name = 'person'
    
    def get_queryset(self):
        if self.request.user.is_superuser:
            return Person.objects.all()
        else:
            try:
                profile = self.request.user.profile
                return Person.objects.filter(location__in=profile.locations.all())
            except UserProfile.DoesNotExist:
                return Person.objects.none()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['events'] = self.object.location.events.all()
        context['comments'] = self.object.comments.all()
        context['comment_form'] = CommentForm()
        return context

class EventListView(LoginRequiredMixin, ListView):
    """View for listing events"""
    model = Event
    template_name = 'nilm/event_list.html'
    context_object_name = 'events'
    
    def get_queryset(self):
        if self.request.user.is_superuser:
            return Event.objects.all()
        else:
            try:
                profile = self.request.user.profile
                return Event.objects.filter(location__in=profile.locations.all())
            except UserProfile.DoesNotExist:
                return Event.objects.none()

class EventDetailView(LoginRequiredMixin, DetailView):
    """View for displaying event details"""
    model = Event
    template_name = 'nilm/event_detail.html'
    context_object_name = 'event'
    
    def get_queryset(self):
        if self.request.user.is_superuser:
            return Event.objects.all()
        else:
            try:
                profile = self.request.user.profile
                return Event.objects.filter(location__in=profile.locations.all())
            except UserProfile.DoesNotExist:
                return Event.objects.none()

class EventByLocationView(LoginRequiredMixin, ListView):
    model = Event
    template_name = 'nilm/events_by_location.html'
    context_object_name = 'events'
    paginate_by = 12

    def get_queryset(self):
        location_id = self.kwargs['location_id']
        return Event.objects.filter(location_id=location_id).order_by('start_time')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        location_id = self.kwargs['location_id']
        context['location'] = get_object_or_404(Location, pk=location_id)
        return context

@login_required
def add_comment(request, person_id):
    """View for adding comments to a person"""
    person = get_object_or_404(Person, id=person_id)
    
    # Check if user has access to this person's location
    if not request.user.is_superuser:
        try:
            profile = request.user.profile
            if person.location not in profile.locations.all():
                messages.error(request, "You don't have access to this person.")
                return redirect('nilm:person_detail', pk=person_id)
        except UserProfile.DoesNotExist:
            messages.error(request, "You don't have access to this person.")
            return redirect('nilm:person_detail', pk=person_id)
    
    if request.method == 'POST':
        form = CommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.person = person
            comment.author = request.user
            comment.save()
            messages.success(request, "Comment added successfully.")
            return redirect('nilm:person_detail', pk=person_id)
    else:
        form = CommentForm()
    
    return redirect('nilm:person_detail', pk=person_id)

# Plausible daily household schedule used as placeholder when no real events exist.
_DAILY_SCHEDULE = [
    {'hour':  6, 'minute': 30, 'type': 'LIGHTS',          'duration':  8, 'class_name': 'NORMAL'},
    {'hour':  7, 'minute':  0, 'type': 'KETTLE',           'duration':  5, 'class_name': 'NORMAL'},
    {'hour':  7, 'minute': 10, 'type': 'MICROWAVE',        'duration':  3, 'class_name': 'NORMAL'},
    {'hour':  7, 'minute': 30, 'type': 'FRIDGE',           'duration':  2, 'class_name': 'NORMAL'},
    {'hour':  9, 'minute':  0, 'type': 'WASHING MACHINE',  'duration': 92, 'class_name': 'NORMAL'},
    {'hour': 10, 'minute': 45, 'type': 'IRON',             'duration': 22, 'class_name': 'NORMAL'},
    {'hour': 12, 'minute':  0, 'type': 'MICROWAVE',        'duration':  4, 'class_name': 'NORMAL'},
    {'hour': 12, 'minute': 15, 'type': 'KETTLE',           'duration':  4, 'class_name': 'NORMAL'},
    {'hour': 14, 'minute': 30, 'type': 'TV',               'duration': 90, 'class_name': 'NORMAL'},
    {'hour': 16, 'minute': 30, 'type': 'KETTLE',           'duration':  5, 'class_name': 'NORMAL'},
    {'hour': 18, 'minute': 30, 'type': 'OVEN',             'duration': 52, 'class_name': 'NORMAL'},
    {'hour': 18, 'minute': 35, 'type': 'FRIDGE',           'duration':  3, 'class_name': 'NORMAL'},
    {'hour': 19, 'minute': 30, 'type': 'LIGHTS',           'duration':180, 'class_name': 'NORMAL'},
    {'hour': 20, 'minute': 30, 'type': 'TV',               'duration':120, 'class_name': 'NORMAL'},
    {'hour': 23, 'minute':  0, 'type': 'LIGHTS',           'duration': 25, 'class_name': 'NORMAL'},
]


def get_placeholder_events(location, date):
    """Return a plausible timed sequence of demo Event objects (unsaved) for `date`.

    If `date` is today, truncates to events whose start_time <= now.
    If a past date, returns the complete daily sequence.
    """
    now = timezone.localtime()
    today = now.date()
    events = []
    for item in _DAILY_SCHEDULE:
        # Build the wall-clock time in the active timezone, not the server's.
        start = timezone.make_aware(
            datetime.datetime.combine(date, datetime.time(item['hour'], item['minute']))
        )
        if date == today and start > now:
            continue  # future — not shown yet
        end = start + datetime.timedelta(minutes=item['duration'])
        events.append(Event(
            location=location,
            start_time=start,
            end_time=end,
            type=item['type'],
            class_name=item['class_name'],
            description='',
        ))
    return list(reversed(events))  # latest first


def _build_location_map_json(locations_qs):
    """Build JSON list of {pk, description, lat, lon} for a queryset of Locations."""
    data = []
    for loc in locations_qs:
        if not loc.location:
            continue
        try:
            lat, lon = (float(x.strip()) for x in loc.location.split(','))
            data.append({'pk': loc.pk, 'description': loc.description, 'lat': lat, 'lon': lon})
        except ValueError:
            pass
    return json.dumps(data)


def ensure_user_has_data_and_events(user):
    import random
    from .models import Location, Event, UserProfile
    
    # 1. If not superuser, assign random locations if they have none
    if not user.is_superuser:
        try:
            profile = user.profile
            if profile.locations.count() == 0:
                all_locs = list(Location.objects.all())
                if all_locs:
                    profile.locations.add(*random.sample(all_locs, min(3, len(all_locs))))
        except UserProfile.DoesNotExist:
            pass

    # 2. Get user locations
    if user.is_superuser:
        user_locs = list(Location.objects.all())
    else:
        try:
            user_locs = list(user.profile.locations.all())
        except UserProfile.DoesNotExist:
            user_locs = []

    # 3. Seed events if the user's locations have none
    if user_locs and Event.objects.filter(location__in=user_locs).count() == 0:
        types = ['FRIDGE', 'OVEN', 'KETTLE', 'MICROWAVE', 'WASHING MACHINE', 'IRON', 'LIGHTS', 'TV']
        classes = ['NORMAL', 'UNEXPECTED', 'ANORMAL', 'ALERT']
        now = timezone.now()
        
        for i in range(24):
            loc = random.choice(user_locs)
            etype = random.choice(types)
            eclass = random.choice(classes)
            duration = random.randint(5, 120)
            start = now - timezone.timedelta(hours=random.randint(0, 48), minutes=random.randint(0, 59))
            end = start + timezone.timedelta(minutes=duration)
            
            Event.objects.create(
                location=loc,
                start_time=start,
                end_time=end,
                type=etype,
                class_name=eclass,
                description=f"Funcionamiento detectado de {etype}."
            )


class DashboardView(LoginRequiredMixin, ListView):
    """Dashboard view showing overview of user's accessible data"""
    model = Location
    template_name = 'nilm/dashboard.html'
    context_object_name = 'locations'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.is_superuser:
            try:
                locs = list(request.user.profile.locations.all())
                if len(locs) == 1:
                    return redirect('nilm:location_detail', pk=locs[0].pk)
            except UserProfile.DoesNotExist:
                pass
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        ensure_user_has_data_and_events(self.request.user)
        if self.request.user.is_superuser:
            return Location.objects.all()
        else:
            try:
                profile = self.request.user.profile
                return profile.locations.all()
            except UserProfile.DoesNotExist:
                return Location.objects.none()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        locations_qs = self.get_queryset()
        if self.request.user.is_superuser:
            context['total_persons'] = Person.objects.count()
            context['total_events'] = Event.objects.count()
        else:
            try:
                profile = self.request.user.profile
                accessible_locations = profile.locations.all()
                context['total_persons'] = Person.objects.filter(location__in=accessible_locations).count()
                context['total_events'] = Event.objects.filter(location__in=accessible_locations).count()
            except UserProfile.DoesNotExist:
                context['total_persons'] = 0
                context['total_events'] = 0
        context['location_map_json'] = _build_location_map_json(locations_qs)
        return context


class ProfileView(LoginRequiredMixin, FormView):
    """View for displaying and updating the logged-in user's profile details"""
    template_name = 'nilm/profile.html'
    form_class = UserProfileUpdateForm
    success_url = reverse_lazy('nilm:profile')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Your profile was updated successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile, created = UserProfile.objects.get_or_create(user=self.request.user)
        context['profile'] = profile
        context['locations'] = profile.locations.all() if not self.request.user.is_superuser else Location.objects.all()
        return context


class AdminProfileListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """View for listing all user profiles (admin only)"""
    model = User
    template_name = 'nilm/admin_profile_list.html'
    context_object_name = 'users'

    def test_func(self):
        return self.request.user.is_superuser

    def get_queryset(self):
        return User.objects.all().select_related('profile').prefetch_related('profile__locations')


class AdminProfileUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """View for admins to update a user's details and assign locations"""
    model = User
    template_name = 'nilm/admin_profile_update.html'
    form_class = UserProfileUpdateForm

    def test_func(self):
        return self.request.user.is_superuser

    def get_success_url(self):
        return reverse('nilm:admin_profile_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user_obj = self.get_object()
        profile, created = UserProfile.objects.get_or_create(user=user_obj)
        
        if self.request.method == 'POST':
            context['location_form'] = UserLocationAssignmentForm(
                self.request.POST,
                instance=profile
            )
        else:
            context['location_form'] = UserLocationAssignmentForm(
                instance=profile
            )
        return context

    def form_valid(self, form):
        user_obj = form.save()
        profile, created = UserProfile.objects.get_or_create(user=user_obj)
        
        location_form = UserLocationAssignmentForm(
            self.request.POST,
            instance=profile
        )
        if location_form.is_valid():
            location_form.save()
            messages.success(self.request, f"Profile for {user_obj.username} updated successfully.")
            return redirect(self.get_success_url())
        else:
            return self.render_to_response(self.get_context_data(form=form))


class EventLoadMoreView(LoginRequiredMixin, ListView):
    """Paginated event loader for infinite scroll — supports cross-date continuation."""
    model = Event
    template_name = 'nilm/partials/event_cards_page.html'
    context_object_name = 'recent_events'
    paginate_by = 10

    def get_queryset(self):
        ensure_user_has_data_and_events(self.request.user)
        location_id = self.request.GET.get('location_id')
        if not location_id:
            self._base_qs = Event.objects.none()
            self._selected_date = None
            return Event.objects.none()

        if self.request.user.is_superuser:
            base_qs = Event.objects.filter(location_id=location_id)
        else:
            try:
                profile = self.request.user.profile
                if profile.locations.filter(id=location_id).exists():
                    base_qs = Event.objects.filter(location_id=location_id)
                else:
                    self._base_qs = Event.objects.none()
                    self._selected_date = None
                    return Event.objects.none()
            except UserProfile.DoesNotExist:
                self._base_qs = Event.objects.none()
                self._selected_date = None
                return Event.objects.none()

        self._base_qs = base_qs
        date_str = self.request.GET.get('date')
        self._selected_date = None
        if date_str:
            try:
                self._selected_date = datetime.date.fromisoformat(date_str)
                base_qs = base_qs.filter(start_time__date=self._selected_date)
            except ValueError:
                pass
        return base_qs.order_by('-start_time')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            page_num = int(self.request.GET.get('page', 1))
        except (ValueError, TypeError):
            page_num = 1
        context['show_date_header'] = (page_num == 1 and self.request.GET.get('date') is not None)
        context['page_date'] = self._selected_date
        return context

    def render_to_response(self, context, **response_kwargs):
        page_obj = context.get('page_obj')
        has_next = page_obj.has_next() if page_obj else False
        response = super().render_to_response(context, **response_kwargs)
        response['X-Has-Next'] = 'true' if has_next else 'false'
        # When this date is exhausted, tell the client the previous date with real events
        if not has_next and self._selected_date and self._base_qs is not None:
            prev_date = (
                self._base_qs
                .filter(start_time__date__lt=self._selected_date)
                .order_by('-start_time')
                .values_list('start_time__date', flat=True)
                .first()
            )
            if prev_date:
                response['X-Next-Date'] = prev_date.isoformat()
        return response


from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter
from drf_spectacular.openapi import OpenApiTypes
from .serializers import (
    EventIngestionSerializer, EventIngestionResponseSerializer,
    MeasurementIngestionSerializer, MeasurementIngestionResponseSerializer,
    DeviceCheckSerializer, DeviceCheckResponseSerializer,
)
from .permissions import IsEdgeDevice
from .models import Measurement


class EventIngestionView(APIView):
    """Receive a detected appliance event from an edge device."""
    authentication_classes = []
    permission_classes = [IsEdgeDevice]

    @extend_schema(
        request=EventIngestionSerializer,
        responses={
            201: EventIngestionResponseSerializer,
            400: OpenApiResponse(description="Invalid payload or device not found"),
            401: OpenApiResponse(description="Missing or invalid Authorization token"),
        },
        summary="Ingest appliance event",
        description=(
            "Called by DOMOBOI edge devices when NILM detects a power-step event "
            "(appliance switched on or off). The location is resolved automatically "
            "from the registered device."
        ),
        tags=["Edge Device API"],
    )
    def post(self, request, *args, **kwargs):
        serializer = EventIngestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        try:
            device = get_object_or_404(Device, device_id=d['device_id'])
            event = Event.objects.create(
                location=device.location,
                start_time=d['start_time'],
                end_time=d['end_time'],
                type=d['type'],
                class_name=d['class_name'],
                description=d['description'],
            )
            return Response({"status": "success", "event_id": event.id}, status=201)
        except Exception as exc:
            return Response({"error": str(exc)}, status=400)


class MeasurementIngestionView(APIView):
    """Receive a raw electrical measurement segment from an edge device."""
    authentication_classes = []
    permission_classes = [IsEdgeDevice]

    @extend_schema(
        request=MeasurementIngestionSerializer,
        responses={
            201: MeasurementIngestionResponseSerializer,
            400: OpenApiResponse(description="Invalid payload or device not found"),
            401: OpenApiResponse(description="Missing or invalid Authorization token"),
        },
        summary="Ingest measurement segment",
        description=(
            "Called by DOMOBOI edge devices to store a raw power-reading window "
            "(typically 30 samples over 3 seconds around a power-step event), "
            "or by the Tuya cloud daemon to store periodic energy snapshots. "
            "For DOMOBOI devices: `readings` is a numeric array and `value` is the "
            "net Watt step-change. "
            "For Tuya devices: `telemetry` carries the full snapshot "
            "(power_w, voltage_v, current_a, energy_kwh, …); `readings` and `value` "
            "are auto-filled from telemetry.power_w if omitted."
        ),
        tags=["Edge Device API"],
    )
    def post(self, request, *args, **kwargs):
        serializer = MeasurementIngestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            device = Device.objects.get(device_id=d['device_id'])
        except Device.DoesNotExist:
            return Response(
                {"error": f"Device {d['device_id']} is not registered."},
                status=404,
            )

        # Idempotent on (device, timestamp), which carries a unique constraint.
        # An edge device draining its offline buffer will legitimately re-send
        # rows it already delivered — if that returned an error, sender.py's
        # drain loop would abort on the first one and the buffer would never
        # empty. Re-posting the same measurement is a no-op, not a failure.
        #
        # timestamp mirrors what Measurement.save() would derive from
        # start_time; it has to be passed explicitly because it is the key.
        meas, created = Measurement.objects.get_or_create(
            device=device,
            timestamp=d['start_time'],
            defaults={
                'start_time': d['start_time'],
                'end_time': d['end_time'],
                'readings': d['readings'],
                'value': d['value'] if d['value'] is not None else 0.0,
                'features': d.get('features'),
                'telemetry': d.get('telemetry'),
            },
        )
        return Response(
            {
                "status": "success" if created else "duplicate",
                "measurement_id": meas.id,
            },
            status=201 if created else 200,
        )


class DeviceConfigCheckView(APIView):
    """Check whether a device is registered and configured in the system."""
    authentication_classes = []
    permission_classes = [IsEdgeDevice]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='device_id', type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY, required=True,
                description="Unique device identifier to look up",
            )
        ],
        responses={
            200: DeviceCheckResponseSerializer,
            400: OpenApiResponse(description="Missing device_id parameter"),
            401: OpenApiResponse(description="Missing or invalid Authorization token"),
        },
        summary="Check device registration",
        description=(
            "Edge devices call this on startup to verify they are registered. "
            "Returns status='ok' with device details if found, or status='NOK' "
            "if the device_id is not in the database. Both cases return HTTP 200 — "
            "check the `status` field."
        ),
        tags=["Edge Device API"],
    )
    def get(self, request, *args, **kwargs):
        device_id = request.GET.get('device_id')
        if not device_id:
            try:
                device_id = request.data.get('device_id')
            except Exception:
                pass
        if not device_id:
            return Response({"error": "Missing device_id parameter"}, status=400)
        try:
            device = Device.objects.get(device_id=device_id)
            return Response({
                "status": "ok",
                "device_id": device.device_id,
                "model": device.model,
                # location is nullable — an unassigned device is still registered
                "location": device.location.description if device.location else None,
            })
        except Device.DoesNotExist:
            return Response({"status": "NOK", "message": f"Device {device_id} is not configured"})
        except Exception as exc:
            return Response({"error": str(exc)}, status=400)

    @extend_schema(
        request=DeviceCheckSerializer,
        responses={200: DeviceCheckResponseSerializer},
        summary="Check device registration (POST)",
        description="Same as GET but accepts device_id in the request body.",
        tags=["Edge Device API"],
    )
    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)



