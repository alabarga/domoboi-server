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

    def test_new_types_in_selector(self):
        url = reverse('nilm:location_appliances', args=[self.loc.pk])
        html = self.client.get(url).content.decode()
        for code, icon in [('HEATER', 'heat'), ('COOKTOP', 'cooking'), ('WATER_HEATER', 'water_heater')]:
            self.assertIn(f'value="{code}"', html)
            self.assertIn(f'>{icon}<', html)
        self.client.post(url, {'type': ['COOKTOP', 'WATER_HEATER']})
        self.assertEqual(set(self.loc.appliances.values_list('type', flat=True)), {'COOKTOP', 'WATER_HEATER'})

    def test_no_access_404(self):
        url = reverse('nilm:location_appliances', args=[self.other.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {'type': ['TV']}).status_code, 404)


class DetectionTests(TestCase):
    def test_segments_fridge_oven_and_spike(self):
        import datetime as dt
        from nilm import detection as det
        t0 = dt.datetime(2026, 10, 1, 12, 0)
        # (minuto, W): base 60 W, nevera 150 W 20 min, pico aislado de 10 s, horno 2 kW con 1 bajada de 10 s
        pts = [(0, 60), (10, 210), (30, 60), (40, 900), (40 + 1 / 6, 60),
               (60, 2060), (80, 1400), (80 + 1 / 6, 2060), (100, 60), (120, 60)]
        series = [(t0 + dt.timedelta(minutes=m), float(p)) for m, p in pts]
        clean, spikes = det.remove_spikes(series, 40)
        self.assertEqual(spikes, 2)  # el pico de 900 W y la bajada de 10 s del horno
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


class TuyaEnergyDedupTests(TestCase):
    def test_live_snapshot_add_ele_not_double_counted(self):
        import datetime as dt
        from nilm.models import Device, Measurement
        from reconstruction.signal import build_tuya_energy, _day_bounds
        dev = Device.objects.create(device_id='tuya1', model='m', device_type='TUYA')
        day = dt.date(2026, 10, 1)
        t0, _ = _day_bounds(day)
        at = lambda m: t0 + dt.timedelta(minutes=m)
        rows = [
            (at(10), {'energy_added_kwh': 0.05}),                    # histórico
            (at(40), {'energy_added_kwh': 0.04}),                    # histórico
            (at(45), {'power_w': 80, 'energy_added_kwh': 0.04}),    # sondeo en vivo: repetición
            (at(60), {'power_w': 80, 'energy_added_kwh': 0.04}),    # sondeo en vivo: repetición
        ]
        for ts, tel in rows:
            Measurement.objects.create(device=dev, start_time=ts, end_time=ts, timestamp=ts, telemetry=tel)
        points, total, n = build_tuya_energy(dev, day)
        self.assertEqual((round(total, 3), n), (0.09, 2))
        self.assertEqual(len(points), 1)  # intervalo 10→40 min


class EventPowerFactorTests(TestCase):
    def test_pf_of_added_load(self):
        import datetime as dt
        import math
        from nilm import detection as det
        t0 = dt.datetime(2026, 10, 1, 12, 0)
        at = lambda m: t0 + dt.timedelta(minutes=m)
        # (minuto, P, Q): base 150 W / 100 var; horno +2000 W resistivo; lavadora +600 W con FP 0,7
        pts = [(0, 150, 100), (10, 2150, 100), (20, 2150, 100), (30, 150, 100),
               (40, 750, 712), (50, 750, 712), (60, 150, 100)]
        series = [(at(m), float(p)) for m, p, _ in pts]
        va = {at(m): math.hypot(p, q) for m, p, q in pts}
        oven, washer = det.segment(series, 150, 40, va=va)
        self.assertEqual(oven['pf'], 1.0)
        self.assertEqual(washer['pf'], 0.7)
        self.assertEqual((oven['pf_class'], washer['pf_class']), ('resistivo', 'motor/electrónica'))
        # sin V·I no hay FP
        self.assertIsNone(det.segment(series, 150, 40)[0]['pf'])

    def test_small_motor_on_big_base(self):
        import math
        from nilm import detection as det
        # base 2000 W resistiva + nevera 67 W con FP 0,7 (Q = 68 var)
        pf = det.added_pf(2000, 2000, 2067, math.hypot(2067, 68))
        self.assertAlmostEqual(pf, 0.70, places=2)
        # el método antiguo (ΔP/ΔS) habría dado ≈ 1
        self.assertGreater(67 / (math.hypot(2067, 68) - 2000), 0.95)

    def test_pf_bias_clamped_and_classified(self):
        import datetime as dt
        from nilm import detection as det
        t0 = dt.datetime(2026, 10, 1, 20, 0)
        at = lambda m: t0 + dt.timedelta(minutes=m)
        # lámpara de 110 W; el medidor da S < P en el encendido (sesgo) → Q = 0
        series = [(at(0), 60.0), (at(5), 170.0), (at(30), 60.0)]
        va = {at(0): 60.0, at(5): 165.0, at(30): 60.0}
        (lamp,), _, _ = det.pair_steps(series, 40, va=va)
        self.assertEqual((lamp['pf'], lamp['pf_class'], lamp['pf_approx']), (1.0, 'resistivo', True))
        self.assertEqual([det.pf_class(x) for x in (0.95, 0.85, 0.6, None)],
                         ['resistivo', 'dudoso', 'motor/electrónica', None])


class PairStepsTests(TestCase):
    def test_overlapping_heater_and_fridge(self):
        import datetime as dt
        import math
        from nilm import detection as det
        t0 = dt.datetime(2026, 10, 1, 7, 0)
        at = lambda m: t0 + dt.timedelta(minutes=m)
        # (minuto, P, Q): estufa +1600 W resistiva (2 ciclos) y nevera +100 W / 100 var (FP 0,71) solapadas
        pts = [(0, 60, 20), (5, 1660, 20), (10, 1760, 120), (15, 160, 120),
               (25, 1760, 120), (30, 1660, 20), (35, 60, 20)]
        series = [(at(m), float(p)) for m, p, _ in pts]
        va = {at(m): math.hypot(p, q) for m, p, q in pts}
        pairs, lost_on, lost_off = det.pair_steps(series, 40, va=va)
        self.assertEqual((lost_on, lost_off), (0, 0))
        got = [(p['start'], p['end'], p['delta_w'], p['pf']) for p in pairs]
        self.assertEqual(got, [
            (at(5), at(15), 1600, 1.0),    # estufa, ciclo 1
            (at(10), at(30), 100, 0.71),   # nevera, solapada con los dos ciclos
            (at(25), at(35), 1600, 1.0),   # estufa, ciclo 2
        ])
        sessions = det.group_cycles(pairs)
        heater = next(s for s in sessions if s['delta_w'] == 1600)
        self.assertEqual((heater['n_cycles'], heater['span_min'], heater['duty']), (2, 30.0, 0.67))


class ClassificationTests(TestCase):
    def session(self, delta, pf_class='resistivo', approx=False):
        import datetime as dt
        from django.utils import timezone
        return {'start': timezone.make_aware(dt.datetime(2026, 10, 1, 20, 0)), 'delta_w': delta,
                'pf_class': pf_class, 'pf_approx': approx, 'pf': 1.0, 'span_min': 20.0, 'n_cycles': 3,
                'on_min': 2.0, 'duty': 0.5, 'energy_wh': 300.0}

    def test_candidates_by_pf_power_declared_and_meter(self):
        from nilm.classification import candidates
        hot = candidates(self.session(1700), set(), 0)  # 20 min
        self.assertTrue({'COOKTOP', 'HEATER', 'OVEN'} <= set(hot))
        self.assertNotIn('KETTLE', hot)  # un hervidor no dura 20 min
        short = dict(self.session(1700), span_min=3.0)
        self.assertIn('KETTLE', candidates(short, set(), 0))
        self.assertNotIn('KETTLE', candidates(dict(short, span_min=11.0), set(), 0))
        self.assertFalse({'FRIDGE', 'TV', 'LIGHTS'} & set(hot))
        self.assertEqual(candidates(self.session(1700), {'TV', 'COOKTOP'}, 0), ['COOKTOP'])
        motor = candidates(self.session(600, 'motor/electrónica'), set(), 0)
        self.assertIn('WASHING MACHINE', motor)
        self.assertNotIn('HEATER', motor)
        # FP aproximado (ΔP pequeño): no se filtra por FP; circuito parcial de 285 W: sin cocina
        small = candidates(self.session(90, 'resistivo', approx=True), set(), 285)
        self.assertIn('FRIDGE', small)
        self.assertFalse({'OVEN', 'COOKTOP', 'HEATER'} & set(small))

    def test_build_request_and_parse_answers(self):
        from nilm import classification as cls
        s = self.session(1700)
        body = cls.build_request({'vivienda': 'persona mayor que vive sola'},
                                 [('E1', cls.describe(s, 'E1'), ['COOKTOP', 'OVEN'])])
        self.assertEqual(set(body['questions']['E1']['criteria']), {'COOKTOP', 'OVEN', 'OTHER', 'NOISE'})
        self.assertEqual(set(body['state']['firmas']), {'COOKTOP', 'OVEN'})  # descripciones una sola vez
        self.assertEqual(body['state']['eventos'][0]['inicio'], 'jue 20:00')
        with self.assertRaises(AssertionError):
            cls.build_request({}, [('E%d' % i, {}, ['OVEN']) for i in range(17)])
        for answer, expected in [
            ('COOKTOP', ('COOKTOP', None)),
            ({'answer': 'OVEN', 'probabilities': {'OVEN': 0.7, 'COOKTOP': 0.3}}, ('OVEN', 0.7)),
            ({'COOKTOP': 0.8, 'OVEN': 0.2}, ('COOKTOP', 0.8)),
            ('FRIDGE', (None, None)),  # no estaba entre los criterios
            ({'type': 'choice', 'choice': 'OVEN', 'confidence': 0.42,  # formato real de FreeJev
              'probabilities': {'OVEN': 0.54, 'OTHER': 0.41, 'NOISE': 0.05}}, ('OVEN', 0.42)),
        ]:
            self.assertEqual(cls.parse_answers({'answers': {'E1': answer}}, body)['E1'], expected)


class DetectEventsCommandTests(TestCase):
    def setUp(self):
        import datetime as dt
        from django.utils import timezone
        from nilm.models import Device, Measurement
        self.loc = Location.objects.create(description='Casa', address='Calle Secreta 1')
        self.dev = Device.objects.create(device_id='tuya-x', model='m', device_type='TUYA', location=self.loc)
        t0 = timezone.make_aware(dt.datetime(2026, 10, 1, 3, 0))
        # vitro de madrugada: 3 ciclos de 1700 W × 2 min (resistiva: S = P)
        for m, p in [(0, 60), (5, 1760), (7, 60), (10, 1760), (12, 60), (15, 1760), (17, 60), (40, 60)]:
            ts = t0 + dt.timedelta(minutes=m)
            Measurement.objects.create(device=self.dev, start_time=ts, end_time=ts, timestamp=ts,
                                       telemetry={'power_w': p, 'current_a': p / 230, 'voltage_v': 230})

    def run_cmd(self, answer):
        from unittest import mock
        from django.core.management import call_command
        from django.test import override_settings
        resp = {'answers': {'E1': answer}, 'usage': {}}
        with override_settings(FREEJEV_API_KEY='test'), \
                mock.patch('nilm.classification.call_freejev', return_value=resp) as api:
            call_command('detect_events', '--location', str(self.loc.pk),
                         '--since', '2026-10-01', '--until', '2026-10-02', stdout=open('/dev/null', 'w'))
        return api

    def test_stores_once_and_flags_night_cooking(self):
        from nilm.models import Event
        api = self.run_cmd('COOKTOP')
        self.assertEqual(api.call_count, 1)
        body = api.call_args[0][0]
        self.assertNotIn('Calle Secreta', str(body))
        ev = Event.objects.get()
        self.assertEqual((ev.source, ev.type, ev.class_name, ev.device_id), ('AUTO', 'COOKTOP', 'UNEXPECTED', self.dev.pk))
        api = self.run_cmd('COOKTOP')  # segunda ejecución: ya guardada, no se vuelve a enviar
        self.assertEqual(api.call_count, 0)
        self.assertEqual(Event.objects.count(), 1)

    def test_low_confidence_is_not_stored(self):
        from nilm.models import Event
        self.run_cmd({'choice': 'COOKTOP', 'confidence': 0.2, 'probabilities': {'COOKTOP': 0.3}})
        self.assertEqual(Event.objects.count(), 0)

    def test_noise_is_not_stored_nor_resent(self):
        from nilm.models import Event
        self.run_cmd('NOISE')
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(self.run_cmd('NOISE').call_count, 0)  # ya clasificada: no se paga otra vez


class TuyaPollFrozenTests(TestCase):
    def test_frozen_live_reading_is_skipped(self):
        import datetime as dt
        from types import SimpleNamespace
        from django.utils import timezone
        from nilm.management.commands.tuya_poll import Command
        from nilm.models import Device, Measurement
        dev = Device.objects.create(device_id='tuya-f', model='m', device_type='TUYA')
        t0 = timezone.now() - dt.timedelta(minutes=15)
        frozen = {'power_w': 119.0, 'current_a': 0.752, 'voltage_v': 237.6}
        Measurement.objects.create(device=dev, start_time=t0, end_time=t0, timestamp=t0, telemetry=frozen)

        class Client:
            def __init__(self, metrics):
                self.metrics = metrics

            def read_many(self, ids, errors=None):
                return [SimpleNamespace(device_id='tuya-f', timestamp=timezone.now(), metrics=self.metrics)]

        cmd = Command(stdout=open('/dev/null', 'w'), stderr=open('/dev/null', 'w'))
        self.assertEqual(cmd._poll_once(Client(dict(frozen)), {'tuya-f': dev}, False), (0, 0))
        self.assertEqual(cmd._poll_once(Client(dict(frozen, voltage_v=237.8)), {'tuya-f': dev}, False), (1, 0))
        self.assertEqual(Measurement.objects.filter(device=dev).count(), 2)


class AutoEventCardTests(TestCase):
    def test_auto_event_shows_in_location_detail(self):
        import datetime as dt
        from django.utils import timezone
        from nilm.models import Event
        admin = User.objects.create_superuser('boss', 'b@b.b', 'pw12345!')
        loc = Location.objects.create(description='Casa', address='x')
        day = dt.date(2026, 11, 1)
        start = timezone.make_aware(dt.datetime(2026, 11, 1, 20, 4))
        Event.objects.create(location=loc, source='AUTO', type='COOKTOP', class_name='NORMAL',
                             start_time=start, end_time=start + dt.timedelta(minutes=82),
                             description='1.7 kW · 16 ciclos · 82 min · resistivo', confidence=0.62)
        self.client.force_login(admin)
        html = self.client.get(reverse('nilm:location_detail', args=[loc.pk]), {'date': day.isoformat()}).content.decode()
        self.assertIn('>cooking<', html)
        self.assertIn('16 ciclos', html)
        self.assertIn('62%', html)


class FreeJevRetryTests(TestCase):
    def test_timeout_is_retried_then_succeeds(self):
        from unittest import mock
        import requests
        from nilm import classification as cls
        ok = mock.Mock(status_code=200, json=lambda: {'answers': {}})
        http = mock.Mock(post=mock.Mock(side_effect=[requests.Timeout('slow'), ok]))
        with mock.patch('nilm.classification.time.sleep'):
            self.assertEqual(cls.call_freejev({'q': 1}, 'k', 'https://x', http=http), {'answers': {}})
        self.assertEqual(http.post.call_count, 2)

    def test_auth_error_is_fatal(self):
        from unittest import mock
        from nilm import classification as cls
        http = mock.Mock(post=mock.Mock(return_value=mock.Mock(status_code=402, text='no credits')))
        with self.assertRaises(cls.FreeJevError) as ctx:
            cls.call_freejev({'q': 1}, 'k', 'https://x', http=http)
        self.assertTrue(ctx.exception.fatal)


class FreeJevIdempotencyTests(TestCase):
    def test_key_stable_within_call_unique_across_calls(self):
        from unittest import mock
        import requests
        from nilm import classification as cls
        ok = mock.Mock(status_code=200, json=lambda: {})
        http = mock.Mock(post=mock.Mock(side_effect=[requests.Timeout('slow'), ok, ok]))
        with mock.patch('nilm.classification.time.sleep'):
            cls.call_freejev({'q': 1}, 'k', 'https://x', http=http)  # timeout + reintento
            cls.call_freejev({'q': 1}, 'k', 'https://x', http=http)  # mismo lote, otra ejecución
        keys = [c.kwargs['headers']['Idempotency-Key'] for c in http.post.call_args_list]
        self.assertEqual(keys[0], keys[1])
        self.assertNotEqual(keys[1], keys[2])


class MergeEventsTests(TestCase):
    def setUp(self):
        import datetime as dt
        from django.utils import timezone
        from nilm.models import Device
        self.loc = Location.objects.create(description='Casa', address='x')
        self.dev = Device.objects.create(device_id='tuya-m', model='m', device_type='TUYA', location=self.loc)
        self.at = lambda h, m: timezone.make_aware(dt.datetime(2026, 11, 7, h, m))

    def ev(self, type, start, end, **features):
        from nilm.models import Event
        return Event(location=self.loc, device=self.dev, source='AUTO', type=type, class_name='NORMAL',
                     start_time=start, end_time=end, description='x', confidence=0.6,
                     features=dict({'delta_w': 1700, 'dur_min': (end - start).total_seconds() / 60,
                                    'fp_tipo': 'resistivo', 'fp_aprox': False}, **features))

    def test_adjacent_cooktop_sessions_become_one_event(self):
        from nilm.management.commands.detect_events import merge_events
        # el caso de la captura: 09:03-09:51 y 09:55-10:22, más un hervidor aparte
        new = [self.ev('COOKTOP', self.at(9, 3), self.at(9, 51)), self.ev('COOKTOP', self.at(9, 55), self.at(10, 22)),
               self.ev('KETTLE', self.at(9, 40), self.at(9, 43))]
        out, merged = merge_events(self.dev, new)
        self.assertEqual(merged, 1)
        cook = next(e for e in out if e.type == 'COOKTOP')
        self.assertEqual((cook.start_time, cook.end_time), (self.at(9, 3), self.at(10, 22)))
        self.assertIn('2 sesiones', cook.description)

    def test_merges_with_event_stored_by_previous_run(self):
        from nilm.management.commands.detect_events import merge_events
        stored = self.ev('COOKTOP', self.at(9, 3), self.at(9, 51))
        stored.save()
        out, merged = merge_events(self.dev, [self.ev('COOKTOP', self.at(9, 55), self.at(10, 22))])
        self.assertEqual((out, merged), ([], 1))
        stored.refresh_from_db()
        self.assertEqual(stored.end_time, self.at(10, 22))

    def test_fix_existing(self):
        from django.core.management import call_command
        from nilm.models import Event
        for e in [self.ev('COOKTOP', self.at(9, 3), self.at(9, 51)), self.ev('COOKTOP', self.at(9, 55), self.at(10, 22)),
                  self.ev('KETTLE', self.at(0, 31), self.at(0, 42))]:  # hervidor de 11 min: implausible
            e.save()
        args = ['detect_events', '--fix-existing', '--since', '2026-11-07', '--until', '2026-11-08']
        call_command(*args, '--dry-run', stdout=open('/dev/null', 'w'))
        self.assertEqual(Event.objects.count(), 3)  # dry-run no toca nada
        call_command(*args, stdout=open('/dev/null', 'w'))
        ev = Event.objects.get()
        self.assertEqual((ev.type, ev.start_time, ev.end_time), ('COOKTOP', self.at(9, 3), self.at(10, 22)))


class LocationDetailScrollTests(TestCase):
    def test_empty_day_with_previous_events(self):
        import datetime as dt
        from django.utils import timezone
        from nilm.models import Event
        admin = User.objects.create_superuser('boss2', 'b@b.b', 'pw12345!')
        loc = Location.objects.create(description='Casa', address='x')
        start = timezone.make_aware(dt.datetime(2026, 11, 6, 21, 38))
        Event.objects.create(location=loc, source='AUTO', type='TV', class_name='NORMAL', start_time=start,
                             end_time=start + dt.timedelta(minutes=160), description='55 W · 161 min')
        self.client.force_login(admin)
        html = self.client.get(reverse('nilm:location_detail', args=[loc.pk]), {'date': '2026-11-08'}).content.decode()
        self.assertEqual(html.count('id="events-column"'), 1)  # sin la copia duplicada
        self.assertEqual(html.count('new IntersectionObserver'), 1)
        self.assertIn('data-next-date="2026-11-06"', html)
        self.assertIn('observer.unobserve(spinner);', html)  # re-observa tras cargar: no se queda girando
        self.assertLess(html.index('No hay eventos para este día'), html.index('id="events-scroll-container"'))


class LightRuleTests(TestCase):
    def test_known_light_saved_as_lights(self):
        import datetime as dt
        from django.core.management import call_command
        from django.utils import timezone
        from nilm.models import Device, Event, Measurement
        loc = Location.objects.create(description='Casa', address='x')
        dev = Device.objects.create(device_id='tuya-l', model='m', device_type='TUYA', location=loc)
        t0 = timezone.make_aware(dt.datetime(2026, 11, 3, 4, 0))
        # luz de 36 W a las 04:37 durante 3 h (madrugada) y otra de 36 W a las 08:00 durante 1 min (demasiado corta)
        pts = ([(0, 68)] + [(m, 104) for m in range(37, 217, 15)]  # con el sondeo de cada 15 min
               + [(217, 68), (240, 68), (241, 104), (242, 68), (250, 68)])
        # otro encendido de 36 W dentro del de madrugada (06:00-06:10): se une y sigue UNEXPECTED
        pts += [(120, 140), (130, 104)]
        pts.sort()
        for m, p in pts:
            ts = t0 + dt.timedelta(minutes=m)
            Measurement.objects.create(device=dev, start_time=ts, end_time=ts, timestamp=ts,
                                       telemetry={'power_w': p, 'current_a': p / 230, 'voltage_v': 230})
        args = ['detect_events', '--location', str(loc.pk), '--since', '2026-11-03', '--until', '2026-11-04',
                '--light-w', '36']
        call_command(*args, '--dry-run', stdout=open('/dev/null', 'w'))
        self.assertEqual(Event.objects.count(), 0)
        call_command(*args, stdout=open('/dev/null', 'w'))
        call_command(*args, stdout=open('/dev/null', 'w'))  # repetir no duplica ni vuelve a unir
        ev = Event.objects.get()
        self.assertEqual(ev.features['sesiones'], 2)
        self.assertEqual((ev.type, ev.class_name, ev.source), ('LIGHTS', 'UNEXPECTED', 'AUTO'))
        self.assertEqual(timezone.localtime(ev.start_time).strftime('%H:%M'), '04:37')
        self.assertEqual(loc.appliances.get(type='LIGHTS').power_w, 36)  # recordada para la ejecución horaria

        # la ejecución normal (sin --light-w) aplica la regla por sí sola
        from unittest import mock
        from django.test import override_settings
        Event.objects.all().delete()
        with override_settings(FREEJEV_API_KEY='test'), \
                mock.patch('nilm.classification.call_freejev', return_value={'answers': {}}):
            call_command('detect_events', '--location', str(loc.pk), '--since', '2026-11-03',
                         '--until', '2026-11-04', stdout=open('/dev/null', 'w'))
        self.assertEqual(list(Event.objects.values_list('type', flat=True)), ['LIGHTS'])
