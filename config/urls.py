
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("tests/", include("knowledge.urls")),
    path("schedule/", include("scheduling.urls")),
    path("admin/", admin.site.urls),
    path("courses/", include("courses.urls")),
    path("enrollments/", include("enrollments.urls")),
    path("", include("users.urls")),
    path("", include("pages.urls")),
    path("learning/", include("learning.urls")),
    path("groups/", include("classrooms.urls")),
    path("notifications/", include("notifications.urls")),

]
