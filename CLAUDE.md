# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run development server
python manage.py runserver

# Run with SQLite (development database alias)
python manage.py runserver --database=development

# Apply migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Load seed data (locations, persons, events from ./data/)
python manage.py load_setup_data

# Compile translations
python manage.py compilemessages

# Make new migrations
python manage.py makemigrations

# Run tests
python manage.py test nilm

# Generate model graph (requires django-extensions)
python manage.py graph_models nilm -o nilm_models.png
```

## Architecture

This is a single-Django-app project. All domain logic lives in the `nilm/` app; `domoboi/` is the project configuration package only.

### Settings & secrets

- `domoboi/settings.py` — main settings; reads DB config from env vars (`DOMOBOI_DB_*`), defaults to PostgreSQL.
- `domoboi/local_settings.py` — git-ignored override; must define `SECRET_KEY` and `EDGE_API_TOKEN`. Falls back to env vars if absent.
- Two database aliases: `default` (PostgreSQL, used in production) and `development` (SQLite at `db.sqlite3`).
- `USE_TZ = False` — datetimes are naive (no timezone info stored).

### Data models (`nilm/models.py`)

The domain is home electricity monitoring via Non-Intrusive Load Monitoring (NILM):

- **Location** — a monitored home; has `PlainLocationField` (lat/lng).
- **Device** — sensor hardware at a location; `is_active` checks for measurements in the last 60 minutes.
- **Person** — resident at a location.
- **UserProfile** — extends Django `User` with a `ManyToManyField` to `Location` (access control). Auto-created via `post_save` signal on `User`.
- **Measurement** — raw electrical reading segment from a device; stores a `readings` JSON array. `value` is auto-populated with the mean of `readings` on save.
- **Event** — an appliance usage event detected by NILM (types: FRIDGE, OVEN, etc.; classes: NORMAL, UNEXPECTED, ANORMAL, ALERT).
- **Comment** — user annotation on a Person.

Access control pattern: every view checks `user.is_superuser` first; regular users are restricted to their `profile.locations`.

### Views & URLs

All app URLs are under `/nilm/` (namespace `nilm`). The root `/` redirects to `/admin/`.

Key view groups:
- **Dashboard** (`nilm:dashboard`) — seeds demo data for new users automatically via `ensure_user_has_data_and_events()`.
- **Location views** — list, map, detail, update.
- **Person views** — list (admin only), detail.
- **Event views** — list, detail, by-location, AJAX load-more (`nilm:event_load_more`, returns `X-Has-Next` header).
- **Profile views** — user self-service + admin user management.
- **Edge device API** (CSRF-exempt, token-authenticated via `Authorization: Token <EDGE_API_TOKEN>`):
  - `POST /nilm/api/events/` — ingest a detected appliance event.
  - `POST /nilm/api/measurements/` — ingest a raw measurement segment.
  - `GET|POST /nilm/api/device/` — device config check (returns `status: ok` or `status: NOK`).

### Admin

Uses `django-unfold` with a custom `UnfoldAdminSite` (`CustomAdminSite` in `nilm/admin.py`) that injects device/location metrics into the admin dashboard. The custom site replaces `admin.site` at module load, requiring all models to be re-registered at the bottom of `admin.py`.

Sidebar navigation is configured in `UNFOLD["SIDEBAR"]` in `settings.py` (Spanish labels: Personas, Domicilios, Dispositivos, Datos, Eventos).

### Internationalization

- Default language: Spanish (`es`). Also supports Catalan (`ca`).
- Translation files in `locale/`.
- `LocaleMiddleware` is active; use `gettext_lazy as _` for all user-facing strings.

### Static files

WhiteNoise serves static files in production (`STATICFILES_STORAGE = CompressedManifestStaticFilesStorage`). Run `python manage.py collectstatic` before deployment.

### Templates

Templates live in `nilm/templates/nilm/`. Partials (used for AJAX responses) are in `nilm/templates/nilm/partials/`. Login template is at `nilm/templates/registration/login.html`.

### Seed / fixture data

`data/` directory contains JSON fixtures for locations, persons, and events used by the `load_setup_data` management command. The `DashboardView` also auto-seeds random events for new users at login time.

### Notebooks

`notebooks/` contains standalone scripts for interacting with external hardware:
- `collect.py` / `listen.py` — Raspberry Pi sensor data collection.
- `tuya.ipynb` — Tuya smart device integration.
