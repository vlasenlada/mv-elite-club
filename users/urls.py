
from django.urls import path
from django.contrib.auth import views as auth_views



from .views import (
    register,
    student_dashboard,
    join_lesson,
    teacher_dashboard,
    review_submission,
    update_lesson_status,
    update_meeting_link,
    edit_teacher_lesson,
    school_admin_dashboard,
    update_enrollment_status,
    assign_lesson_teacher,
    assign_enrollment_teacher,

)




urlpatterns = [
    path("register/", register, name="register"),

    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="users/login.html"
        ),
        name="login"
    ),

    path(
        "logout/",
        auth_views.LogoutView.as_view(
            next_page="home"
        ),
        name="logout"
    ),
    
    path("dashboard/", student_dashboard, name="student_dashboard"),
    path("lessons/<int:lesson_id>/join/", join_lesson, name="join_lesson"),
    
    path(
        "teacher/dashboard/",
        teacher_dashboard,
        name="teacher_dashboard"
    ),


    path(
        "teacher/submissions/<int:submission_id>/review/",
        review_submission,
        name="review_submission"
    ),

    path(
        "teacher/lessons/<int:lesson_id>/status/",
        update_lesson_status,
        name="update_lesson_status"
    ),

   
    path(
        "teacher/lessons/<int:lesson_id>/meeting-link/",
        update_meeting_link,
        name="update_meeting_link"
    ),
 

    path(
        "teacher/lessons/<int:lesson_id>/edit/",
        edit_teacher_lesson,
        name="edit_teacher_lesson"
    ),

    path(
        "school-admin/",
        school_admin_dashboard,
        name="school_admin_dashboard"
    ),
    
    path(
        "school-admin/enrollments/<int:enrollment_id>/status/",
        update_enrollment_status,
        name="update_enrollment_status"
    ),


    path(
        "school-admin/lessons/<int:lesson_id>/assign-teacher/",
        assign_lesson_teacher,
        name="assign_lesson_teacher",
    ),

    path(
        "school-admin/enrollments/<int:enrollment_id>/assign-teacher/",
        assign_enrollment_teacher,
        name="assign_enrollment_teacher",
    ),

]
