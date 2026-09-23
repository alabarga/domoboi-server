import csv
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from nilm.models import Device


class Command(BaseCommand):
    help = 'Create users from data/device_users.csv and assign them to device locations'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without writing to the database',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        csv_path = Path(settings.BASE_DIR) / 'data' / 'device_users.csv'

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be saved'))

        # Group rows by phone: {phone: [(device_id, name), ...]}
        phone_rows = defaultdict(list)
        phone_name = {}
        with open(csv_path, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                phone = row['phone'].strip()
                device_id = row['device'].strip()
                name = row['name'].strip()
                phone_rows[phone].append(device_id)
                if phone not in phone_name:
                    phone_name[phone] = name

        for phone, device_ids in phone_rows.items():
            name = phone_name[phone]
            if not dry_run:
                user, created = User.objects.get_or_create(username=phone)
                user.first_name = name
                user.set_password(phone)
                user.save()
            else:
                created = not User.objects.filter(username=phone).exists()

            status = 'Created' if created else 'Updated'
            self.stdout.write(self.style.SUCCESS(f'{status} user: {phone} ({name})'))

            for device_id in set(device_ids):
                try:
                    device = Device.objects.get(device_id=device_id)
                except Device.DoesNotExist:
                    self.stdout.write(self.style.ERROR(f'  Device not found: {device_id}'))
                    continue

                if device.location is None:
                    self.stdout.write(self.style.WARNING(f'  Device {device_id} has no location assigned'))
                    continue

                if not dry_run:
                    user.profile.locations.add(device.location)

                self.stdout.write(f'  -> Location: {device.location} (via {device_id})')

        self.stdout.write(self.style.SUCCESS('Done.'))
