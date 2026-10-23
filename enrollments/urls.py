
from django.urls import path
from .views import enroll_course

urlpatterns = [
    path(
        "enroll/<int:pk>/",
        enroll_course,
        name="enroll_course"
    ),
]
