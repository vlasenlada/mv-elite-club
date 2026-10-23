
from django.shortcuts import render
from courses.models import Course


def home(request):
    courses = Course.objects.filter(is_published=True)

    context = {
        "courses": courses,
    }

    return render(request, "pages/home.html", context)
