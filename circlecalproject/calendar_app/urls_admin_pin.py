from django.urls import path
from . import views

urlpatterns = [
    path('', views.admin_pin_view, name='admin_pin'),
    path('manage/', views.admin_pin_manage, name='admin_pin_manage'),
]
