
from django.urls import path
from .views import enroll_course
from . import trial_views

urlpatterns = [
    path("trial/sent/", trial_views.trial_success, name="trial_success"),
    path("trial/<int:pk>/", trial_views.trial_request, name="trial_request"),
    path("trial/requests/<int:pk>/", trial_views.trial_detail, name="trial_detail"),
    path(
        "enroll/<int:pk>/",
        enroll_course,
        name="enroll_course"
    ),
]
