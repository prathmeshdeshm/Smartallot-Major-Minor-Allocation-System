from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static
from core import views as core_views # Import core views

urlpatterns = [
    path('admin/', admin.site.urls),

  # Auth routes
    path('accounts/student/login/', core_views.student_login, name='student_login'),
    # Registration disabled - students are imported by admin
    # path('accounts/student/register/', core_views.student_register, name='student_register'),
    path('accounts/admin/login/', core_views.admin_login, name='admin_login'),
    path('accounts/logout/', core_views.logout_view, name='logout'),
    path('accounts/password/forgot/', core_views.forgot_password, name='forgot_password'),
    path('accounts/password/reset/', core_views.reset_password_with_otp, name='reset_password_with_otp'),
    
    # App URLs
    path('', include('allotment.urls')),
]

# ✅ Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)