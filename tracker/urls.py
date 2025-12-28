from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # The Login Page
    path('login/', auth_views.LoginView.as_view(template_name='tracker/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    
    # We will build this next, but let's define it now so it doesn't crash
    path('', views.dashboard, name='dashboard'),
    path('project/<int:project_id>/', views.project_detail, name='project_detail'),
    path('pole/<int:pole_id>/', views.pole_detail, name='pole_detail'),
    path('inspection/<int:project_id>/', views.admin_project_inspection, name='admin_inspection'),
    path('view/<uuid:client_uuid>/', views.client_view, name='client_view'),
    path('make_admin/', views.create_admin_user, name='make_admin'),
    path('project/<int:project_id>/complete/', views.mark_project_completed, name='mark_project_completed'),
]