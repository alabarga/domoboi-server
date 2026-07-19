
# 🏠 Home Electricity Monitoring Service — Django Web App

## 📖 Overview

This platform is a web-based management system for monitoring residential electricity consumption through installed sensors. It provides a structured way to manage monitored locations, the people living there, collected measurements, detected events, and related user-generated comments.

The system is built using **Django** and designed for secure, authenticated access, with role-based features for regular and administrative users.

## Components

- Django Unfold admin https://unfoldadmin.com/
- Multilingual support
  - https://medium.com/@sakhawy/multilingual-support-in-django-5706e1e144a8
  - https://testdriven.io/blog/multiple-languages-in-django/
  - https://medium.com/@patelaniket1207/building-a-multi-language-website-with-django-a-comprehensive-guide-f6b9017c8bde

## 📦 Features

### 🔐 Authentication
- All views require user login.
- Users can only access the locations assigned to them.
- Admin users have full access to all data.

### 🗺️ Location Management
- View a list of assigned locations (for regular users).
- View all locations (for admin users).
- Display locations both as a list and on an interactive map.
- Edit location descriptions.
- View and manage persons assigned to each location.

### 👥 Person Management
- Admin users can view a list of all registered persons.
- View detailed information for a person:
  - Personal data (name, age, gender, description)
  - Associated events
  - User comments (with ability to add new comments)

### 📊 Event Management
- List of detected events per person or location.
- Event details include type, class, start and end time, and description.

---

## 📐 Data Models

| Model       | Fields                                                                 |
|:------------|:------------------------------------------------------------------------|
| **Location**  | `description`, `address`, `geo_coordinates`                           |
| **Person**    | `name`, `description`, `age`, `gender`, `location (ForeignKey)`        |
| **UserProfile** | `user (OneToOne)`, `locations (ManyToMany)`                          |
| **Measurement** | `location (ForeignKey)`, `timestamp`, `value`                         |
| **Event**      | `location (ForeignKey)`, `start_time`, `end_time`, `type`, `class`, `description` |
| **Comment**    | `person (ForeignKey)`, `timestamp`, `message`                         |

---

## 🖥️ Admin Panel Features

- Manage locations, persons, events, measurements, and comments.
- Assign one or multiple locations to users via the `UserProfile` model.
- Grant or restrict access to location data based on assigned locations.

---

## 🖼️ Views Overview

| View         | Access           | Description                                                      |
|:--------------|:------------------|:-----------------------------------------------------------------|
| **Locations (List & Map)** | Logged users | View assigned locations (admin sees all) |
| **Location Detail** | Logged users | View and edit location details; list associated persons |
| **Persons (List)** | Admin only | List of all persons |
| **Person Detail** | Logged users | View person info, events, comments, and add new comments |
| **Events (List)** | Logged users | List of events for locations the user has access to |

---

## 🚀 Getting Started

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Apply migrations:
   ```bash
   python manage.py migrate
   ```
4. Create a superuser:
   ```bash
   python manage.py createsuperuser
   ```
5. Run the development server:
   ```bash
   python manage.py runserver
   ```

---

## 📌 Tech Stack

- **Python 3.12+**
- **Django 5.0+**
- **PostgreSQL** (recommended, but can start with SQLite)
- **Leaflet.js / OpenStreetMap** for interactive maps (planned)

---

## Devices

### Raspeberry Pi details
- https://www.ebay.co.uk/itm/226699698887?_skw=IPEM+PiHat+Lite&itmmeta=01K01DFMSBG9YQV0XPM2GK3DVA&hash=item34c85ae2c7%3Ag%3AxOkAAOSwMrpn-4Gf&itmprp=enc%3AAQAKAAAA0FkggFvd1GGDu0w3yXCmi1dWo2KtSpxPcS4S1NCaTP4nfaBsg2BTUp4%2FhAEnpk42R1JydTEFAsedHcsJDUilJ%2BzIsO7evFIctna2ixN4AkTkbrRS3tFcURKZYvRZKF223F6iXuSxIIAZDfrlJmZSO%2BHCUeKffCiLU9QZXnI4m4F0yKg%2Bt6C5Rc9jrDwJEFKFtJGzaQZp81Cy%2Fs9ItbdQjVufZ6P3RTZSpEKiuQTBd1iR1vjskDbjdXAtn5%2F95SZSjwoiFPfjJn%2B1vGXGF28hyos%3D%7Ctkp%3ABk9SR-LMvq2AZg&var=525872633140

- https://github.com/david00/rpi-power-monitor/
- https://power-monitor.dalbrecht.tech/order/

## 📈 Roadmap

- Real-time event alerting via WebSockets
- Measurement visualization dashboards
- Role-based permissions enhancements
- REST API / GraphQL support for external integrations

---

## 📝 License

[MIT License](LICENSE)
