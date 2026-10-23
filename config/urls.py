
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("courses/", include("courses.urls")),
    path("enrollments/", include("enrollments.urls")),
    path("", include("users.urls")),
    path("", include("pages.urls")),
]
