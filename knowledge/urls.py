from django.urls import path
from . import views

app_name = "knowledge"
urlpatterns = [
    path("", views.catalog, name="index"),
    path("sitemap.xml", views.sitemap, name="sitemap"),
    path("attempt/<uuid:attempt_id>/question/<int:number>/", views.question, name="question"),
    path("attempt/<uuid:attempt_id>/summary/", views.summary, name="summary"),
    path("attempt/<uuid:attempt_id>/result/", views.result, name="result"),
    path("<str:subject>/", views.catalog, name="subject"),
    path("<str:subject>/nmt/", views.catalog, {"nmt": True}, name="nmt"),
    path("<str:subject>/<slug:slug>/", views.detail, name="detail"),
    path("<str:subject>/<slug:slug>/start/", views.start, name="start"),
]
