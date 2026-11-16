from django.urls import path
from . import views

urlpatterns = [
    path("", views.calendar_view, name="lesson_calendar"),
    path("lessons/<int:pk>/", views.lesson_detail, name="lesson_detail"),
]
