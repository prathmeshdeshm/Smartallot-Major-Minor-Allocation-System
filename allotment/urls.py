from django.urls import path
from . import views
from . import merit_list_views

urlpatterns = [
    # Dashboards
    path('', views.home, name='home'),
    path('dashboard/admin/', views.admin_dashboard, name='admin_dashboard'),
    path('dashboard/student/', views.student_dashboard, name='student_dashboard'),
    path('validate/', views.validate_student_form, name='validate_student_form'),  # NEW: Student validation

    # Course Management
    path('manage-courses/', views.manage_courses, name='manage_courses'),
    path('courses/create/', views.course_create, name='course_create'),
    path('courses/<str:course_type>/<int:pk>/update/', views.course_update, name='course_update'),
    path('courses/<str:course_type>/<int:pk>/delete/', views.course_delete, name='course_delete'),

    # ---------- MINOR RULES ----------
    # DISABLED: Rules are READ-ONLY and protected from all modifications
    # DISABLED: path('rules/<int:branch_pk>/create/', views.rule_create, name='rule_create'),
    # DISABLED: path('rules/<int:pk>/edit/', views.rule_edit, name='rule_edit'),
    # DISABLED: path('rules/<int:pk>/toggle/', views.rule_toggle_active, name='rule_toggle'),
    # DISABLED: path('rules/<int:pk>/delete/', views.rule_delete, name='rule_delete'),

    # ---------- OE RULES ----------
    # DISABLED: Rules are READ-ONLY and protected from all modifications
    # DISABLED: path('rules/oe/<int:oe_pk>/add/', views.oe_rule_create, name='oe_rule_create'),
    # DISABLED: path('rules/oe/<int:pk>/edit/', views.oe_rule_edit, name='oe_rule_edit'),
    # DISABLED: path('rules/oe/<int:pk>/toggle/', views.oe_rule_toggle_active, name='oe_rule_toggle'),
    # DISABLED: path('rules/oe/<int:pk>/delete/', views.oe_rule_delete, name='oe_rule_delete'),

    # Reports
    path('reports/download/csv/', views.download_csv_report, name='download_csv_report'),
    path('reports/allocations/', views.allocation_report, name='allocation_report'),
    path('reports/allocations/export-excel/', views.export_allocation_excel, name='export_allocation_excel'),
    
    # ⭐ MERIT LIST & ALLOCATION REPORT (NEW)
    path('dashboard/admin/merit-list/', merit_list_views.admin_merit_list_report, name='admin_merit_list_report'),
    path('api/merit-list/', merit_list_views.merit_list_api, name='merit_list_api'),

    # ======================== NEW FEATURE URLS ========================
    
    # Student Features
    path('notifications/', views.student_notifications, name='student_notifications'),
    path('feedback/submit/', views.submit_feedback, name='submit_feedback'),
    path('preference-history/', views.preference_history, name='preference_history'),
    path('settings/', views.user_settings, name='user_settings'),
    
    # Admin Features
    path('dashboard/admin/announcements/', views.manage_announcements, name='manage_announcements'),
    path('dashboard/admin/feedback/', views.view_feedback, name='view_feedback'),
    path('dashboard/admin/audit-logs/', views.audit_logs, name='audit_logs'),
    path('dashboard/admin/export/', views.export_data, name='export_data'),
    path('dashboard/admin/bulk-import/', views.bulk_import_students, name='bulk_import_students'),
    
    # ✅ NEW: Monitoring and Validation Features
    path('dashboard/admin/capacity-dashboard/', views.capacity_dashboard, name='capacity_dashboard'),
    path('dashboard/admin/capacity-dashboard/branch/<int:branch_id>/', views.capacity_detail_branch, name='capacity_detail_branch'),
    path('dashboard/admin/capacity-dashboard/oe/<int:oe_id>/', views.capacity_detail_oe, name='capacity_detail_oe'),
    path('dashboard/admin/capacity-dashboard/export/excel/', views.capacity_export_excel, name='capacity_export_excel'),
    path('dashboard/admin/validation-report/', views.validation_report, name='validation_report'),
    
    # 🔟 REASSESSMENT FLOW
    path('reassessment/submit/', views.submit_reassessment, name='submit_reassessment'),
    path('reassessment/update/', views.update_reassessment, name='update_reassessment'),
    path('reassessment/update/<int:reassessment_id>/', views.update_reassessment, name='update_reassessment_detail'),
    path('reassessment/my-submissions/', views.my_reassessments, name='my_reassessments'),
    path('dashboard/admin/reassessments/', views.manage_reassessments, name='manage_reassessments'),
    path('dashboard/admin/reassessment/<int:pk>/approve/', views.approve_reassessment, name='approve_reassessment'),
    path('dashboard/admin/reassessment/<int:pk>/reject/', views.reject_reassessment, name='reject_reassessment'),
    
    # 👥 STUDENT MANAGEMENT (ADMIN)
    path('dashboard/admin/students/', views.student_list, name='student_list'),
    path('dashboard/admin/students/create/', views.student_create, name='student_create'),
    path('dashboard/admin/students/<int:student_id>/', views.student_detail, name='student_detail'),
    path('dashboard/admin/students/<int:student_id>/edit/', views.student_edit, name='student_edit'),
    path('dashboard/admin/students/<int:student_id>/delete/', views.student_delete, name='student_delete'),
    path('dashboard/admin/students/bulk-action/', views.student_bulk_action, name='student_bulk_action'),
    
    # NEW: EXCEL IMPORT & STUDENT VALIDATION
    path('dashboard/admin/import-excel/', views.import_students_excel, name='import_students_excel'),
    path('dashboard/admin/import-results/', views.import_student_results_excel, name='import_student_results_excel'),
    path('dashboard/admin/absconding-students/', views.manage_absconding_students, name='manage_absconding_students'),
    path('dashboard/admin/absconding-students/<int:absconding_id>/', views.absconding_student_detail, name='absconding_student_detail'),
    path('dashboard/admin/absconding-students/<int:absconding_id>/register/', views.register_absconding_student, name='register_absconding_student'),
    path('dashboard/admin/absconding-students/<int:absconding_id>/delete/', views.delete_absconding_student, name='delete_absconding_student'),
    path('dashboard/admin/validate-capacity/', views.validate_capacity, name='validate_capacity'),
]