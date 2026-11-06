from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from nilm.models import UserProfile, Location

class UserProfileTests(TestCase):
    def setUp(self):
        self.username = 'testuser'
        self.password = 'testpassword123'
        self.user = User.objects.create_user(
            username=self.username,
            password=self.password,
            email='test@example.com'
        )
        
        self.admin_username = 'adminuser'
        self.admin = User.objects.create_superuser(
            username=self.admin_username,
            password=self.password,
            email='admin@example.com'
        )

    def test_profile_created_automatically(self):
        """Verify that creating a user automatically creates a UserProfile."""
        profile = UserProfile.objects.filter(user=self.user).first()
        self.assertIsNotNone(profile)
        
        new_user = User.objects.create_user(username='newuser', password='password')
        new_profile = UserProfile.objects.filter(user=new_user).first()
        self.assertIsNotNone(new_profile)

    def test_profile_view_requires_login(self):
        """Verify profile page redirects to login for unauthenticated users."""
        response = self.client.get(reverse('nilm:profile'))
        self.assertRedirects(response, f"/login/?next={reverse('nilm:profile')}")

    def test_profile_view_authenticated(self):
        """Verify authenticated user can access the profile page and view details."""
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(reverse('nilm:profile'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'nilm/profile.html')

    def test_admin_views_restriction(self):
        """Verify only admins can access list and update views."""
        # Non-admin user tries to access
        self.client.login(username=self.username, password=self.password)
        list_response = self.client.get(reverse('nilm:admin_profile_list'))
        self.assertEqual(list_response.status_code, 403)
        
        update_response = self.client.get(reverse('nilm:admin_profile_update', kwargs={'pk': self.user.pk}))
        self.assertEqual(update_response.status_code, 403)
        
        # Admin user access
        self.client.login(username=self.admin_username, password=self.password)
        list_response_admin = self.client.get(reverse('nilm:admin_profile_list'))
        self.assertEqual(list_response_admin.status_code, 200)
        self.assertTemplateUsed(list_response_admin, 'nilm/admin_profile_list.html')
        
        update_response_admin = self.client.get(reverse('nilm:admin_profile_update', kwargs={'pk': self.user.pk}))
        self.assertEqual(update_response_admin.status_code, 200)
        self.assertTemplateUsed(update_response_admin, 'nilm/admin_profile_update.html')


from nilm.models import Event
from django.utils import timezone
from datetime import timedelta

class NILMExplorerAndScrollTests(TestCase):
    def setUp(self):
        self.username = 'testuser'
        self.password = 'testpassword123'
        self.user = User.objects.create_user(
            username=self.username,
            password=self.password
        )
        self.location = Location.objects.create(
            description="Test House",
            address="Test Address"
        )
        self.user.profile.locations.add(self.location)

    def test_location_properties(self):
        """Verify safety score and alert counting properties on Location."""
        self.assertGreaterEqual(self.location.score, 35)
        self.assertLessEqual(self.location.score, 100)
        self.assertIn(self.location.score_class, ['score-green', 'score-orange', 'score-red'])
        
        now = timezone.now()
        Event.objects.create(
            location=self.location,
            start_time=now - timedelta(hours=2),
            end_time=now - timedelta(hours=1),
            type='FRIDGE',
            class_name='ALERT',
            description='Test Alert 24h'
        )
        Event.objects.create(
            location=self.location,
            start_time=now - timedelta(days=3),
            end_time=now - timedelta(days=3, minutes=30),
            type='TV',
            class_name='ALERT',
            description='Test Alert last week'
        )
        Event.objects.create(
            location=self.location,
            start_time=now - timedelta(hours=1),
            end_time=now,
            type='LIGHTS',
            class_name='NORMAL',
            description='Normal event'
        )
        self.assertEqual(self.location.alerts_last_24h, 1)
        self.assertEqual(self.location.alerts_last_week, 2)

    def test_event_duration(self):
        """Verify Event duration property returns correct duration in minutes."""
        now = timezone.now()
        event = Event.objects.create(
            location=self.location,
            start_time=now - timedelta(minutes=45),
            end_time=now,
            type='OVEN',
            class_name='NORMAL'
        )
        self.assertEqual(event.duration_minutes, 45)

    def test_infinite_scroll_endpoint(self):
        """Verify EventLoadMoreView handles pagination and custom headers."""
        self.client.login(username=self.username, password=self.password)
        # No events: must not seed fake ones
        response = self.client.get(reverse('nilm:event_load_more') + f"?location_id={self.location.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Has-Next'], 'false')
        self.assertEqual(Event.objects.filter(location=self.location).count(), 0)

        # Needs more than one page: paginate_by is 10, so exactly 10 events
        # would yield a single page and X-Has-Next would be 'false'.
        now = timezone.now()
        for i in range(15):
            Event.objects.create(
                location=self.location,
                start_time=now - timedelta(hours=i),
                end_time=now - timedelta(hours=i, minutes=30),
                type='LIGHTS',
                class_name='NORMAL'
            )


        response_page1 = self.client.get(reverse('nilm:event_load_more') + f'?location_id={self.location.id}&page=1')
        self.assertEqual(response_page1.status_code, 200)
        self.assertEqual(response_page1['X-Has-Next'], 'true')

        response_page2 = self.client.get(reverse('nilm:event_load_more') + f'?location_id={self.location.id}&page=2')
        self.assertEqual(response_page2.status_code, 200)
        self.assertEqual(response_page2['X-Has-Next'], 'false')

    def test_translation_switch(self):
        """Verify that translation set_language endpoint toggles active locale."""
        self.client.login(username=self.username, password=self.password)
        response = self.client.post(reverse('set_language'), {
            'language': 'ca',
            'next': reverse('nilm:dashboard')
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.cookies.get('django_language').value, 'ca')




class LocationAppliancesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='resident', password='pw12345!')
        self.loc = Location.objects.create(description='Casa', address='Calle 1')
        self.other = Location.objects.create(description='Otra', address='Calle 2')
        self.user.profile.locations.add(self.loc)
        self.client.login(username='resident', password='pw12345!')

    def test_post_syncs_appliances(self):
        from nilm.models import Appliance
        Appliance.objects.create(location=self.loc, type='TV')
        url = reverse('nilm:location_appliances', args=[self.loc.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {'type': ['FRIDGE', 'OVEN', 'BOGUS']})
        self.assertRedirects(r, reverse('nilm:location_detail', args=[self.loc.pk]))
        self.assertEqual(set(self.loc.appliances.values_list('type', flat=True)), {'FRIDGE', 'OVEN'})

    def test_no_access_404(self):
        url = reverse('nilm:location_appliances', args=[self.other.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {'type': ['TV']}).status_code, 404)


class DetectionTests(TestCase):
    def test_segments_fridge_oven_and_spike(self):
        import datetime as dt
        from nilm import detection as det
        t0 = dt.datetime(2026, 10, 1, 12, 0)
        # (minuto, W): base 60 W, nevera 150 W 20 min, pico aislado, horno 2 kW con 1 bajada de 1 min
        pts = [(0, 60), (10, 210), (30, 60), (40, 900), (42, 60),
               (60, 2060), (80, 1400), (81, 2060), (100, 60), (120, 60)]
        series = [(t0 + dt.timedelta(minutes=m), float(p)) for m, p in pts]
        clean, spikes = det.remove_spikes(series, 40)
        self.assertEqual(spikes, 2)  # el pico de 900 W y la bajada de 1 min del horno
        segs = det.segment(clean, 60, 40)
        self.assertEqual(len(segs), 2)
        fridge, oven = segs
        self.assertEqual((fridge['duration_min'], fridge['plateau_w']), (20.0, 150))
        self.assertEqual(oven['duration_min'], 40.0)
        self.assertEqual(oven['plateau_w'], 2000)
        self.assertEqual(oven['n_steps'], 0)
        # sin quitar picos, la bajada aparece como 2 escalones internos
        self.assertEqual(det.segment(series, 60, 40)[-1]['n_steps'], 2)
        self.assertAlmostEqual(det.integrate_kwh(series, t0, t0 + dt.timedelta(minutes=10)), 0.01)
