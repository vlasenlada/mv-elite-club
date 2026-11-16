
from django.urls import path
from .views import course_detail, course_list

urlpatterns = [
    path("", course_list, name="course_list"),
    path("<int:pk>/", course_detail, name="course_detail"),
    path("<slug:slug>/", course_detail, name="course_detail_slug"),
]
