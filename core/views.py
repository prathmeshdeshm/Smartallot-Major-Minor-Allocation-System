from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.views.decorators.http import require_http_methods

import random
import string

from .forms import (
    StudentRegistrationForm,
    PasswordResetRequestForm,
    OTPPasswordResetForm,
)
from allotment.models import Student, ImportedStudent


# -------------------------
# Student Registration
# -------------------------
def student_register(request):
    """
    Handles new student user + Student profile creation.
    Only for normal students, not admins.
    """
    if request.user.is_authenticated:
        if request.user.is_staff or request.user.is_superuser:
            return redirect('admin_dashboard')
        return redirect('student_dashboard')

    if request.method == 'POST':
        form = StudentRegistrationForm(request.POST)
        if form.is_valid():
            try:
                user = form.save()
            except Exception:
                form.add_error('email', 'This email or roll number is already registered.')
            else:
                login(request, user)
                messages.success(request, "Registration successful. Welcome to SmartAllot!")
                return redirect('student_dashboard')
    else:
        form = StudentRegistrationForm()

    return render(request, 'core/student_register.html', {'form': form})


# -------------------------
# Student Login (Name + Roll Number Validation)
# -------------------------
def student_login(request):
    """
    Validate students using their Name and Roll Number from imported records.
    No password needed - just verification against ImportedStudent database.
    """
    # Redirect if already authenticated as admin
    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        return redirect('admin_dashboard')
    
    # Check if student is already validated in session
    if request.session.get('validated_student_id'):
        return redirect('student_dashboard')

    if request.method == 'POST':
        full_name = request.POST.get('full_name', '').strip()
        roll_no = request.POST.get('roll_no', '').strip()
        
        if not full_name or not roll_no:
            messages.error(request, "Please enter both Name and Roll Number.")
            return render(request, 'core/student_login.html', {})
        
        # Validate against ImportedStudent records
        try:
            # Try exact match first
            imported_student = ImportedStudent.objects.get(
                roll_no__iexact=roll_no,
                full_name__iexact=full_name
            )
            
            # Create or get User for this student (if doesn't exist)
            from django.contrib.auth.models import User
            user, user_created = User.objects.get_or_create(
                username=roll_no.lower(),
                defaults={
                    'email': f"{roll_no}@student.edu",
                    'first_name': full_name.split()[0] if full_name else '',
                    'last_name': ' '.join(full_name.split()[1:]) if len(full_name.split()) > 1 else '',
                    'is_active': True
                }
            )
            
            # Get or create Student record for this imported student
            student, created = Student.objects.get_or_create(
                roll_no=roll_no.upper(),
                defaults={
                    'user': user,
                    'name': imported_student.full_name,
                    'department': imported_student.major_branch if imported_student.major_branch != 'GENERAL' else 'CSE',
                    'marks': imported_student.marks if imported_student.marks is not None else 0,
                    'percentage': imported_student.percentage if imported_student.percentage is not None else 0,
                    'is_validated': False,
                    'email': f"{roll_no}@student.edu"
                }
            )
            
            # Update existing student data — but only overwrite with valid imported data
            # Don't overwrite good data from auto-allocation with None/GENERAL
            if not created:
                student.user = user
                student.name = imported_student.full_name
                # Only update department if imported value is valid (not GENERAL)
                if imported_student.major_branch and imported_student.major_branch != 'GENERAL':
                    student.department = imported_student.major_branch
                # Only update marks/percentage if imported values are meaningful
                if imported_student.marks is not None and imported_student.marks > 0:
                    student.marks = imported_student.marks
                if imported_student.percentage is not None and imported_student.percentage > 0:
                    student.percentage = imported_student.percentage
                if not student.email or student.email.endswith('@student.edu'):
                    student.email = f"{roll_no}@student.edu"
                student.save()
            
            # Store validated student in session
            request.session['validated_student_id'] = student.id
            request.session['student_roll_no'] = student.roll_no
            request.session['student_name'] = student.name
            
            messages.success(request, f"Welcome, {student.name}! You can now submit your preferences.")
            return redirect('student_dashboard')
            
        except ImportedStudent.DoesNotExist:
            messages.error(request, 
                "Invalid Name or Roll Number. Please check your details or contact the administrator if you were recently added.")
        except ImportedStudent.MultipleObjectsReturned:
            messages.error(request, 
                "Multiple records found. Please contact administrator to resolve this issue.")

    return render(request, 'core/student_login.html', {})


# -------------------------
# Admin Login
# -------------------------
def admin_login(request):
    """
    Login view for admins (staff/superuser).
    """
    if request.user.is_authenticated:
        if request.user.is_staff or request.user.is_superuser:
            return redirect('admin_dashboard')
        return redirect('student_dashboard')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()

            if not (user.is_staff or user.is_superuser):
                messages.error(request, "You are not authorized as admin.")
                return redirect('student_login')
            else:
                login(request, user)
                return redirect('admin_dashboard')
    else:
        form = AuthenticationForm()

    return render(request, 'core/admin_login.html', {'form': form})


# -------------------------
# Logout
# -------------------------
@require_http_methods(["GET", "POST"])
def logout_view(request):
    """
    Logs the user out and redirects to the home page.
    Handles both session-based student login and Django auth login.
    Accepts both GET and POST because some templates use logout links.
    """
    # Clear student session if exists
    if 'validated_student_id' in request.session:
        student_name = request.session.get('student_name', 'Student')
        request.session.flush()
        messages.success(request, f"Goodbye {student_name}! You have been logged out successfully.")
    # Clear Django authenticated user
    elif request.user.is_authenticated:
        logout(request)
        messages.success(request, "You have been logged out successfully.")
    
    return redirect('home')


# -------------------------
# Password Reset with OTP (Email)
# -------------------------
def _generate_otp(length=6):
    return ''.join(random.choices(string.digits, k=length))


def forgot_password(request):
    """
    Step 1: User enters email, we send OTP and store it in session.
    """
    if request.method == 'POST':
        form = PasswordResetRequestForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            try:
                # ✅ FIX: Use filter().first() instead of get() to handle duplicate emails
                user = User.objects.filter(email=email).first()
                if not user:
                    form.add_error('email', 'No account found with this email.')
                else:
                    otp = _generate_otp()
                    # Store details in session
                    request.session['password_reset_user_id'] = user.id
                    request.session['password_reset_otp'] = otp
                    request.session['password_reset_time'] = timezone.now().isoformat()

                    subject = "SmartAllot Password Reset OTP"
                    message = f"Your OTP for password reset is: {otp}\nThis code is valid for 10 minutes."
                    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None)

                    try:
                        # ✅ FIX: Add debugging and fallback to console backend if email fails
                        send_mail(subject, message, from_email, [email])
                        print(f"✅ Email sent successfully to {email} with OTP: {otp}")
                        messages.success(request, f"OTP has been sent to {email}. Check your email.")
                        return redirect('reset_password_with_otp')
                    except Exception as e:
                        # ✅ FIX: Provide more detailed error message
                        print(f"❌ Email sending failed: {str(e)}")
                        print(f"Email config - HOST: {settings.EMAIL_HOST}, USER: {settings.EMAIL_HOST_USER}, PORT: {settings.EMAIL_PORT}")
                        
                        # Fallback: Show OTP on page for testing (remove in production)
                        messages.warning(request, f"Note: Email sending failed. For testing, OTP is: {otp}")
                        request.session['test_otp_displayed'] = True
                        return redirect('reset_password_with_otp')
            except Exception as e:
                print(f"❌ Unexpected error: {str(e)}")
                form.add_error(None, f"An error occurred: {str(e)}")
    else:
        form = PasswordResetRequestForm()

    return render(request, 'core/forgot_password.html', {'form': form})


def reset_password_with_otp(request):
    """
    Step 2: User enters OTP + new password.
    """
    user_id = request.session.get('password_reset_user_id')
    session_otp = request.session.get('password_reset_otp')

    if not user_id or not session_otp:
        messages.error(request, "Password reset session expired. Please request a new OTP.")
        return redirect('forgot_password')

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        messages.error(request, "User not found. Please request a new OTP.")
        return redirect('forgot_password')

    if request.method == 'POST':
        form = OTPPasswordResetForm(request.POST)
        if form.is_valid():
            otp_entered = form.cleaned_data['otp']
            if otp_entered != session_otp:
                form.add_error('otp', 'Invalid OTP.')
            else:
                new_password = form.cleaned_data['new_password1']
                user.set_password(new_password)
                user.save()

                # Clear session
                for key in ['password_reset_user_id', 'password_reset_otp', 'password_reset_time']:
                    if key in request.session:
                        del request.session[key]

                messages.success(request, "Password successfully reset. You can now log in.")
                return redirect('student_login')
    else:
        form = OTPPasswordResetForm()

    return render(request, 'core/reset_password_with_otp.html', {'form': form})
