from django.urls import path
from . import views

app_name = 'nilm'

urlpatterns = [
    # Dashboard
    path('', views.DashboardView.as_view(), name='dashboard'),
    
    # Location views
    path('locations/', views.LocationListView.as_view(), name='location_list'),
    path('locations/map/', views.LocationMapView.as_view(), name='location_map'),
    path('locations/<int:pk>/', views.LocationDetailView.as_view(), name='location_detail'),
    path('locations/<int:pk>/edit/', views.LocationUpdateView.as_view(), name='location_update'),
    
    # Person views
    path('persons/', views.PersonListView.as_view(), name='person_list'),
    path('persons/<int:pk>/', views.PersonDetailView.as_view(), name='person_detail'),
    
    # Event views
    path('events/', views.EventListView.as_view(), name='event_list'),
    path('events/<int:pk>/', views.EventDetailView.as_view(), name='event_detail'),
    path('locations/<int:location_id>/events/', views.EventByLocationView.as_view(), name='events_by_location'),
    
    # Comment views
    path('persons/<int:person_id>/add-comment/', views.add_comment, name='add_comment'),

    # Profile views
    path('profile/', views.ProfileView.as_view(), name='profile'),
    path('profile/users/', views.AdminProfileListView.as_view(), name='admin_profile_list'),
    path('profile/users/<int:pk>/', views.AdminProfileUpdateView.as_view(), name='admin_profile_update'),

    # Events API
    path('events/load-more/', views.EventLoadMoreView.as_view(), name='event_load_more'),

    # Data Ingestion API for Edge devices
    path('api/events/', views.EventIngestionView.as_view(), name='api_event_ingestion'),
    path('api/measurements/', views.MeasurementIngestionView.as_view(), name='api_measurement_ingestion'),
    path('api/device/', views.DeviceConfigCheckView.as_view(), name='api_device_check'),
]
 