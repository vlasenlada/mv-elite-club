
from django.urls import path
from .views import course_detail

urlpatterns = [
    path("<int:pk>/", course_detail, name="course_detail"),
]
