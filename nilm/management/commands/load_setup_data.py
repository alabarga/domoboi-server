import os
import json
from django.core.management.base import BaseCommand
from django.conf import settings
from nilm.models import Location, Person, Event
from django.contrib.auth.models import User

class Command(BaseCommand):
    help = 'Load setup data for locations, persons, and events from ./data/'

    def handle(self, *args, **options):
        data_dir = os.path.join(settings.BASE_DIR, 'data')
        self.stdout.write(self.style.NOTICE('Loading setup data from %s' % data_dir))

        # Load locations
        locations_path = os.path.join(data_dir, 'locations.json')
        with open(locations_path, 'r') as f:
            locations_data = json.load(f)
        loc_map = {}
        for loc in locations_data:
            obj, created = Location.objects.get_or_create(
                description=loc['description'],
                defaults={
                    'address': loc.get('address', ''),
                    'geo_coordinates': loc.get('geo_coordinates', ''),
                }
            )
            loc_map[loc['id']] = obj
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created location: {obj}'))
            else:
                self.stdout.write(f'Location exists: {obj}')

        # Load persons
        persons_path = os.path.join(data_dir, 'persons.json')
        with open(persons_path, 'r') as f:
            persons_data = json.load(f)
        person_map = {}
        for p in persons_data:
            location = loc_map.get(p['location_id'])
            if not location:
                self.stdout.write(self.style.ERROR(f"Location id {p['location_id']} not found for person {p['name']}"))
                continue
            obj, created = Person.objects.get_or_create(
                name=p['name'],
                location=location,
                defaults={
                    'description': p.get('description', ''),
                    'age': p.get('age', 0),
                    'gender': p.get('gender', 'O'),
                }
            )
            person_map[p['id']] = obj
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created person: {obj}'))
            else:
                self.stdout.write(f'Person exists: {obj}')

        # Load events
        events_path = os.path.join(data_dir, 'events.json')
        with open(events_path, 'r') as f:
            events_data = json.load(f)
        for e in events_data:
            location = loc_map.get(e['location_id'])
            if not location:
                self.stdout.write(self.style.ERROR(f"Location id {e['location_id']} not found for event {e.get('class', '')}"))
                continue
            obj, created = Event.objects.get_or_create(
                location=location,
                start_time=e['start_time'],
                end_time=e['end_time'],
                type=e.get('type', 'other'),
                class_name=e.get('class', ''),
                defaults={
                    'description': e.get('description', ''),
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created event: {obj}'))
            else:
                self.stdout.write(f'Event exists: {obj}')

        self.stdout.write(self.style.SUCCESS('Setup data loaded successfully.')) 