from django.urls import path
from . import views
from django.contrib.auth import views as auth_views


urlpatterns = [
    # Auth
    path('login/', auth_views.LoginView.as_view(template_name='tracker/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),

    # App
    path('', views.dashboard, name='dashboard'),
    path('project/<int:project_id>/', views.project_detail, name='project_detail'),
    path('project/<int:project_id>/add_item/', views.create_project_item, name='create_project_item'),
    path('project/<int:project_id>/complete/', views.mark_project_completed, name='mark_project_completed'),
    path('pole/<int:pole_id>/', views.pole_detail, name='pole_detail'),
    path('evidence/<int:evidence_id>/delete/', views.delete_evidence, name='delete_evidence'),
    path('admin-inspection/<int:project_id>/', views.admin_project_inspection, name='admin_project_inspection'),

    # --- CLIENT LINKS ---
    # 1. Master Dashboard (Optional: Shows all cities for a Client Org)
    path('client/<uuid:client_uuid>/', views.client_dashboard, name='client_dashboard'),
    
    # 2. PROJECT VIEW (The "Magic Link" for a specific City/Project)
    # RESTORED: Uses UUID so no login is required
    path('view/<uuid:client_uuid>/', views.client_city_view, name='client_view'),
    path('issue/report/<int:pole_id>/', views.report_issue, name='report_issue'),

    # Issue handeling 
    path('project/<int:project_id>/issues/', views.project_issues, name='project_issues'),
    path('issue/resolve/<int:issue_id>/', views.resolve_issue, name='resolve_issue'),

    # --- AUDIT LOGS ---
    path('project/<int:project_id>/logs/', views.project_logs, name='project_logs'),
    path('project/<int:project_id>/logs/export/', views.export_project_logs, name='export_project_logs'),

    # tracker/urls.py
    # Add this line to the list:
    path('temp-admin-fix/', views.create_admin_temp),
]