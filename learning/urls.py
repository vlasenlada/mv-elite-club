
from django.urls import path
from . import views

urlpatterns = [
    path(
        "homework/<int:assignment_id>/",
        views.submit_homework,
        name="submit_homework"
    ),
]
