from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.views.decorators.http import require_http_methods
from django.db.models import Q
import logging
import re

import random
import string

from .forms import (
    StudentRegistrationForm,
    PasswordResetRequestForm,
    OTPPasswordResetForm,
)
from allotment.models import Student, ImportedStudent
from allotment.utils import calculate_percentage_from_marks
from .admin_account import AdminAccountCreationForm, admin_account_exists


logger = logging.getLogger(__name__)


def _roll_number_variants(raw_roll):
    """Build normalized roll-number variants for robust matching."""
    if raw_roll is None:
        return []

    candidates = {str(raw_roll).strip()}
    normalized = []

    for candidate in candidates:
        cleaned = candidate.replace(' ', '').upper()
        if not cleaned:
            continue
        normalized.append(cleaned)

        match = re.match(r'^([0-9]+)\.0+$', cleaned)
        if match:
            normalized.append(match.group(1))

    unique = []
    seen = set()
    for value in normalized:
        if value and value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _build_roll_query(raw_roll):
    variants = _roll_number_variants(raw_roll)
    if not variants:
        return None

    q = Q(roll_no__iexact=variants[0])
    for variant in variants[1:]:
        q |= Q(roll_no__iexact=variant)
    return q


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
        
        if not ImportedStudent.objects.exists():
            messages.error(request, "No imported student records found. Please contact administrator.")
            return render(request, 'core/student_login.html', {})

        try:
            imported_qs = ImportedStudent.objects.filter(
                full_name__iexact=full_name,
            )

            roll_query = _build_roll_query(roll_no)
            if roll_query is not None:
                imported_qs = imported_qs.filter(roll_query)

            imported_student = imported_qs.select_related('import_batch').order_by('-import_batch__imported_at', '-id').first()
            if not imported_student:
                raise ImportedStudent.DoesNotExist

            roll_variants = _roll_number_variants(roll_no)
            canonical_roll = min(roll_variants, key=len) if roll_variants else roll_no.upper()
            
            # Create or get User for this student (if doesn't exist)
            from django.contrib.auth.models import User
            user, user_created = User.objects.get_or_create(
                username=canonical_roll.lower(),
                defaults={
                    'email': f"{canonical_roll}@student.edu",
                    'first_name': full_name.split()[0] if full_name else '',
                    'last_name': ' '.join(full_name.split()[1:]) if len(full_name.split()) > 1 else '',
                    'is_active': True
                }
            )

            # Get or create Student record with robust roll matching.
            student = None
            student_roll_query = _build_roll_query(canonical_roll)
            if student_roll_query is not None:
                student = Student.objects.filter(student_roll_query).first()

            created = False
            resolved_percentage = calculate_percentage_from_marks(
                imported_student.marks,
                fallback_percentage=imported_student.percentage,
            )
            if not student:
                student = Student.objects.create(
                    user=user,
                    name=imported_student.full_name,
                    roll_no=canonical_roll,
                    department=imported_student.major_branch if imported_student.major_branch != 'GENERAL' else 'CSE',
                    marks=imported_student.marks if imported_student.marks is not None else 0,
                    percentage=resolved_percentage,
                    is_validated=False,
                    email=f"{canonical_roll}@student.edu"
                )
                created = True
            
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
                student.percentage = calculate_percentage_from_marks(
                    student.marks,
                    fallback_percentage=(
                        imported_student.percentage
                        if imported_student.percentage is not None
                        else student.percentage
                    ),
                )
                if not student.email or student.email.endswith('@student.edu'):
                    student.email = f"{canonical_roll}@student.edu"
                student.save()
            
            # Store validated student in session
            request.session['validated_student_id'] = student.id
            request.session['student_roll_no'] = student.roll_no
            request.session['student_name'] = student.name
            # Force verification on every new login session.
            request.session['student_verification_complete'] = False
            request.session.pop('validation_step_passed', None)
            request.session.pop('validation_email_otp', None)
            request.session.pop('validation_otp_sent_at', None)
            request.session.pop('validation_otp_sent_to', None)
            request.session.pop('validation_pending_email', None)
            request.session.pop('validation_pending_percentage', None)
            
            messages.success(request, f"Welcome, {student.name}! Please complete verification to continue.")
            return redirect('student_dashboard')
            
        except ImportedStudent.DoesNotExist:
            messages.error(request, 
                "Invalid Name or Roll Number. Please check your details or contact the administrator if you were recently added.")

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

    return render(request, 'core/admin_login.html', {
        'form': form,
        'can_create_admin_account': not admin_account_exists(),
    })


def admin_account_setup(request):
    if admin_account_exists():
        messages.info(request, "An admin account already exists.")
        return redirect('admin_login')

    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        return redirect('admin_dashboard')

    if request.method == 'POST':
        form = AdminAccountCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Admin account created successfully. You are now logged in.")
            return redirect('admin_dashboard')
    else:
        form = AdminAccountCreationForm()

    return render(request, 'core/admin_account_setup.html', {
        'form': form,
    })


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
                        logger.exception(
                            "Password reset OTP email failed for email=%s (host=%s port=%s user=%s): %s",
                            email,
                            settings.EMAIL_HOST,
                            settings.EMAIL_PORT,
                            settings.EMAIL_HOST_USER,
                            e,
                        )
                        
                        # Fallback: Show OTP on page for testing (remove in production)
                        if settings.DEBUG:
                            messages.warning(
                                request,
                                f"Note: Email sending failed ({e.__class__.__name__}: {e}). For testing, OTP is: {otp}"
                            )
                        else:
                            messages.error(request, "Unable to send OTP email right now. Please try again later.")
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
