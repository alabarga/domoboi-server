"""Reconcile the Device table with the meters visible in the Tuya cloud.

`tuya_poll` only polls devices already registered with ``device_type='TUYA'``,
so this command is how they get there. It reports what the cloud exposes versus
what the database knows, and can relabel or create the missing rows.

Usage
-----
    python manage.py sync_tuya_devices --dry-run     # report only
    python manage.py sync_tuya_devices               # relabel existing rows
    python manage.py sync_tuya_devices --create      # also create missing ones

Creating is opt-in because a new Device has no Location, and an unassigned
device cannot receive Events (``Event.location`` is NOT NULL). Registering in
the admin, where a location can be chosen, is usually the better route.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from nilm.models import Device


class Command(BaseCommand):
    help = "Reconcile Device rows with the meters exposed by the Tuya cloud."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report the differences without writing anything.",
        )
        parser.add_argument(
            "--create", action="store_true",
            help="Also create Device rows for cloud meters not yet in the database.",
        )

    def _build_client(self):
        try:
            from tuya_energy import TuyaConfig, TuyaOpenAPI, EnergyClient
        except ImportError as exc:
            raise CommandError(
                "tuya_energy is not installed. Install it with:\n"
                "    pip install -e /path/to/domoboi-tuya"
            ) from exc

        if not settings.TUYA_ACCESS_ID or not settings.TUYA_ACCESS_SECRET:
            raise CommandError("TUYA_ACCESS_ID and TUYA_ACCESS_SECRET must be set.")

        return EnergyClient(TuyaOpenAPI(TuyaConfig(
            access_id=settings.TUYA_ACCESS_ID,
            access_secret=settings.TUYA_ACCESS_SECRET,
            region=settings.TUYA_REGION,
        )))

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        client = self._build_client()

        meters = {d.id: d for d in client.list_devices(energy_only=True)}
        known = {d.device_id: d for d in Device.objects.all()}

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — nothing will be written"))
        self.stdout.write(
            f"{len(meters)} energy meter(s) in the Tuya cloud, "
            f"{len(known)} device(s) in the database"
        )

        relabelled, created, already, unassigned = [], [], [], []

        for tuya_id, meter in sorted(meters.items()):
            device = known.get(tuya_id)

            if device is None:
                if options["create"]:
                    if not dry_run:
                        Device.objects.create(
                            device_id=tuya_id,
                            model=meter.product_name or meter.category or "Tuya meter",
                            device_type="TUYA",
                        )
                    created.append(tuya_id)
                else:
                    unassigned.append((tuya_id, meter.name or meter.product_name or "?"))
            elif device.device_type != "TUYA":
                if not dry_run:
                    device.device_type = "TUYA"
                    device.save(update_fields=["device_type"])
                relabelled.append(tuya_id)
            else:
                already.append(tuya_id)

        for label, items, style in (
            ("already registered", already, self.style.SUCCESS),
            ("relabelled to TUYA", relabelled, self.style.WARNING),
            ("created", created, self.style.SUCCESS),
        ):
            if items:
                self.stdout.write(style(f"  {len(items)} {label}"))
                for item in items:
                    self.stdout.write(f"    {item}")

        if unassigned:
            self.stdout.write(self.style.WARNING(
                f"  {len(unassigned)} cloud meter(s) not in the database "
                f"(pass --create, or add them in the admin with a location):"
            ))
            for tuya_id, name in unassigned:
                self.stdout.write(f"    {tuya_id}  {name}")

        # Registered as TUYA but no longer visible in the cloud — these will be
        # polled and fail every cycle until removed or the cloud account is fixed.
        stale = sorted(
            d.device_id for d in known.values()
            if d.device_type == "TUYA" and d.device_id not in meters
        )
        if stale:
            self.stdout.write(self.style.ERROR(
                f"  {len(stale)} device(s) marked TUYA but absent from the cloud:"
            ))
            for device_id in stale:
                self.stdout.write(f"    {device_id}")

        missing_location = sorted(
            d.device_id for d in known.values()
            if d.device_type == "TUYA" and d.location_id is None
        )
        if missing_location:
            self.stdout.write(self.style.WARNING(
                f"  {len(missing_location)} TUYA device(s) without a location "
                f"(measurements work; events will fail):"
            ))
            for device_id in missing_location:
                self.stdout.write(f"    {device_id}")
