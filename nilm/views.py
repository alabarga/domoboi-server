from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import ListView, DetailView, UpdateView, CreateView
from django.views.generic.edit import FormView
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q
from django.core.paginator import Paginator
from django.contrib.auth.models import User
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

class LocationDetailView(LoginRequiredMixin, DetailView):
    """View for displaying location details"""
    model = Location
    template_name = 'nilm/location_detail.html'
    context_object_name = 'location'
    
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
        context['recent_measurements'] = self.object.measurements.all()[:10]
        
        # Paginate events for the current location, initially show page 1 (6 items)
        events_qs = self.object.events.all()
        paginator = Paginator(events_qs, 6)
        context['recent_events'] = paginator.get_page(1)
        context['has_next_events'] = paginator.num_pages > 1
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

def ensure_user_has_data_and_events(user):
    import random
    from django.utils import timezone
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
    """View for loading paginated events on home dashboard (AJAX)"""
    model = Event
    template_name = 'nilm/partials/event_cards.html'
    context_object_name = 'recent_events'
    paginate_by = 6

    def get_queryset(self):
        ensure_user_has_data_and_events(self.request.user)
        location_id = self.request.GET.get('location_id')
        if not location_id:
            return Event.objects.none()
        
        # Verify access
        if self.request.user.is_superuser:
            return Event.objects.filter(location_id=location_id)
        else:
            try:
                profile = self.request.user.profile
                if profile.locations.filter(id=location_id).exists():
                    return Event.objects.filter(location_id=location_id)
            except UserProfile.DoesNotExist:
                pass
            return Event.objects.none()

    def render_to_response(self, context, **response_kwargs):
        page_obj = context.get('page_obj')
        has_next = page_obj.has_next() if page_obj else False
        response = super().render_to_response(context, **response_kwargs)
        response['X-Has-Next'] = 'true' if has_next else 'false'
        return response


from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.utils.dateparse import parse_datetime
from .models import Measurement
import json

@method_decorator(csrf_exempt, name='dispatch')
class EventIngestionView(View):
    def post(self, request, *args, **kwargs):
        from django.conf import settings
        auth_header = request.headers.get('Authorization', '')
        # Verify simple API token authorization from settings.py
        expected_token = f"Token {getattr(settings, 'EDGE_API_TOKEN', 'default-api-token-value-here')}"
        if auth_header != expected_token:
            return JsonResponse({"error": "Unauthorized"}, status=401)
            
        try:
            data = json.loads(request.body)
            device_id = data.get('device_id')
            start_time_str = data.get('start_time')
            end_time_str = data.get('end_time')
            appliance_type = data.get('type')
            class_name = data.get('class_name', 'NORMAL')
            description = data.get('description', '')
            
            device = get_object_or_404(Device, device_id=device_id)
            location = device.location
            start_time = parse_datetime(start_time_str)
            end_time = parse_datetime(end_time_str)
            
            event = Event.objects.create(
                location=location,
                start_time=start_time,
                end_time=end_time,
                type=appliance_type,
                class_name=class_name,
                description=description
            )
            return JsonResponse({"status": "success", "event_id": event.id}, status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class MeasurementIngestionView(View):
    def post(self, request, *args, **kwargs):
        from django.conf import settings
        from django.utils.dateparse import parse_datetime
        
        auth_header = request.headers.get('Authorization', '')
        expected_token = f"Token {getattr(settings, 'EDGE_API_TOKEN', 'default-api-token-value-here')}"
        if auth_header != expected_token:
            return JsonResponse({"error": "Unauthorized"}, status=401)
            
        try:
            data = json.loads(request.body)
            device_id = data.get('device_id')
            start_time_str = data.get('start_time')
            end_time_str = data.get('end_time')
            readings = data.get('readings', [])
            value = data.get('value')  # delta_p size from edge
            
            device = get_object_or_404(Device, device_id=device_id)
            location = device.location
            start_time = parse_datetime(start_time_str)
            end_time = parse_datetime(end_time_str)
            
            # Save raw telemetry segment
            meas = Measurement.objects.create(
                location=location,
                start_time=start_time,
                end_time=end_time,
                readings=readings,
                value=value if value is not None else 0.0
            )
            
            return JsonResponse({
                "status": "success", 
                "measurement_id": meas.id
            }, status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class DeviceConfigCheckView(View):
    def get(self, request, *args, **kwargs):
        from django.conf import settings
        auth_header = request.headers.get('Authorization', '')
        expected_token = f"Token {getattr(settings, 'EDGE_API_TOKEN', 'default-api-token-value-here')}"
        if auth_header != expected_token:
            return JsonResponse({"error": "Unauthorized"}, status=401)
            
        device_id = request.GET.get('device_id')
        if not device_id:
            try:
                data = json.loads(request.body)
                device_id = data.get('device_id')
            except Exception:
                pass
                
        if not device_id:
            return JsonResponse({"error": "Missing device_id parameter"}, status=400)
            
        try:
            device = Device.objects.get(device_id=device_id)
            return JsonResponse({
                "status": "ok",
                "device_id": device.device_id,
                "model": device.model,
                "location": device.location.description
            }, status=200)
        except Device.DoesNotExist:
            return JsonResponse({
                "status": "NOK",
                "message": f"Device {device_id} is not configured"
            }, status=200)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)



