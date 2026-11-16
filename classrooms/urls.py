from django.urls import path

from . import views

urlpatterns = [
    path("<int:pk>/", views.group_detail, name="group_detail"),
    path("attendance/<int:pk>/", views.update_attendance, name="update_attendance"),
]
