from django.urls import path

from . import views

app_name = 'reconstruction'

urlpatterns = [
    path('', views.DeviceListView.as_view(), name='device_list'),
    path('go/', views.DeviceJumpView.as_view(), name='device_redirect'),
    path('<str:device_id>/', views.DeviceSignalView.as_view(), name='device_signal'),
    path('<str:device_id>/label/<int:measurement_id>/', views.LabelUpdateView.as_view(), name='label_update'),
]
