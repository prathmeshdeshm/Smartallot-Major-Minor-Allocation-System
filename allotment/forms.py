from django import forms
from django.contrib.auth.models import User
from .models import (
    StudentFeedback, Announcement, UserPreferences,
    Notification, EligibilityRule, OEEligibilityRule, PreferenceWindow,
    Student, MinorBranch, OpenElective, Reassessment, StudentImport, StudentResults
)


# ======================== STUDENT VALIDATION FORM (STEP 1) ========================
class StudentValidationForm(forms.Form):
    """Step 1: Validate student identity with roll number and full name"""
    roll_no = forms.CharField(
        label='Roll Number',
        max_length=50,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g., 23BI310560',
            'readonly': 'readonly'
        })
    )
    
    full_name = forms.CharField(
        label='Full Name',
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your full name as per records',
        })
    )


# ======================== STUDENT DATA COLLECTION FORM (STEP 2) ========================
class StudentDataCollectionForm(forms.Form):
    """Step 2: Collect percentage, marks, email, and branch after validation"""
    marks = forms.DecimalField(
        label='Grand Total Marks',
        decimal_places=2,
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'Your total marks',
            'step': '0.01',
            'min': '0'
        })
    )
    
    percentage = forms.DecimalField(
        label='Percentage (%)',
        decimal_places=2,
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your percentage (0-100)',
            'step': '0.01',
            'min': '0',
            'max': '100'
        })
    )
    
    branch = forms.ChoiceField(
        label='Major Branch',
        choices=Student.DEPARTMENTS,
        required=False,  # Not required since it's disabled/readonly in template
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    email = forms.EmailField(
        label='Email Address',
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'your.email@example.com'
        })
    )


# ======================== FEEDBACK FORM ========================
class StudentFeedbackForm(forms.ModelForm):
    class Meta:
        model = StudentFeedback
        fields = ['rating', 'satisfaction_with_allocation', 'comments', 'difficulties_faced', 'suggestions']
        widgets = {
            'rating': forms.RadioSelect(choices=StudentFeedback._meta.get_field('rating').choices, attrs={
                'class': 'form-check-input'
            }),
            'satisfaction_with_allocation': forms.RadioSelect(choices=StudentFeedback._meta.get_field('satisfaction_with_allocation').choices, attrs={
                'class': 'form-check-input'
            }),
            'comments': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Your overall feedback...'
            }),
            'difficulties_faced': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Any difficulties you faced...'
            }),
            'suggestions': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Your suggestions for improvement...'
            }),
        }


# ======================== ELIGIBILITY RULE FORMS ========================
class EligibilityRuleForm(forms.ModelForm):
    class Meta:
        model = EligibilityRule
        fields = ['branch', 'rule_type', 'value', 'is_active']
        widgets = {
            'branch': forms.Select(attrs={'class': 'form-control'}),
            'rule_type': forms.Select(attrs={'class': 'form-control'}),
            'value': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': '{"min_percentage": 75} or {"blocked_departments": ["IT", "ECE"]}'
            }),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class OEEligibilityRuleForm(forms.ModelForm):
    class Meta:
        model = OEEligibilityRule
        fields = ['oe_subject', 'rule_type', 'value', 'is_active']
        widgets = {
            'oe_subject': forms.Select(attrs={'class': 'form-control'}),
            'rule_type': forms.Select(attrs={'class': 'form-control'}),
            'value': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': '{"min_percentage": 75} or {"blocked_departments": ["IT", "ECE"]}'
            }),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


# ======================== ANNOUNCEMENT FORM ========================
class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ['title', 'content', 'is_active', 'expires_at']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Announcement title'
            }),
            'content': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 6,
                'placeholder': 'Announcement content'
            }),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'expires_at': forms.DateTimeInput(attrs={
                'class': 'form-control',
                'type': 'datetime-local'
            }),
        }


# ======================== PREFERENCE WINDOW FORM ========================
class PreferenceWindowForm(forms.ModelForm):
    class Meta:
        model = PreferenceWindow
        fields = ['name', 'start_at', 'end_at', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Window name'
            }),
            'start_at': forms.DateTimeInput(attrs={
                'class': 'form-control',
                'type': 'datetime-local'
            }),
            'end_at': forms.DateTimeInput(attrs={
                'class': 'form-control',
                'type': 'datetime-local'
            }),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


# ======================== USER PREFERENCES FORM ========================
class UserPreferencesForm(forms.ModelForm):
    class Meta:
        model = UserPreferences
        fields = ['receive_email_notifications', 'receive_sms_notifications', 'phone_number', 'theme']
        widgets = {
            'receive_email_notifications': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'receive_sms_notifications': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'phone_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '+91 XXXXXXXXXX',
                'type': 'tel'
            }),
            'theme': forms.Select(attrs={'class': 'form-control'}),
        }


# ======================== BULK IMPORT FORM ========================
class StudentBulkImportForm(forms.Form):
    csv_file = forms.FileField(
        label='Upload CSV File',
        help_text='CSV format: name, roll_no, department, percentage, email, has_backlog',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.csv'
        })
    )


# NEW: EXCEL IMPORT FORM ========================
class ExcelImportForm(forms.Form):
    """Form for uploading Excel file with student data"""
    excel_file = forms.FileField(
        label='Upload Excel File (.xlsx)',
        help_text='Excel format: Full Name, Roll Number, Marks, Percentage, Major Branch',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.xlsx,.xls'
        })
    )


# ======================== EXPORT FORM ========================
class ExportForm(forms.Form):
    EXPORT_CHOICES = [
        ('all_allocations', 'All Allocations'),
        ('by_department', 'By Department'),
        ('by_branch', 'By Branch'),
        ('statistics', 'Statistics'),
        ('appeals', 'Appeals'),
        ('feedback', 'Feedback'),
    ]
    
    export_type = forms.ChoiceField(
        choices=EXPORT_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
        label='What to export?'
    )
    
    file_format = forms.ChoiceField(
        choices=[('csv', 'CSV'), ('pdf', 'PDF'), ('excel', 'Excel')],
        widget=forms.Select(attrs={'class': 'form-control'}),
        label='File format'
    )

# ======================== REASSESSMENT FORMS ========================
class ReassessmentDeclarationForm(forms.ModelForm):
    """Phase 1: Student declares backlog and uploads fail marksheet"""
    class Meta:
        model = Reassessment
        fields = ['has_backlog', 'backlog_subjects', 'backlog_details', 'fail_marksheet']
        widgets = {
            'has_backlog': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'backlog_subjects': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Enter subject codes/names (comma-separated) with backlogs'
            }),
            'backlog_details': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Provide detailed information about your backlogs'
            }),
            'fail_marksheet': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.pdf,.jpg,.jpeg,.png',
                'required': True
            }),
        }
        labels = {
            'has_backlog': 'I have backlog(s)',
            'backlog_subjects': 'Backlog Subjects',
            'backlog_details': 'Backlog Details',
            'fail_marksheet': 'Fail Marksheet (PDF/Image)',
        }


class ReassessmentUpdateForm(forms.ModelForm):
    """Phase 2: Student uploads passing marksheet after results"""
    class Meta:
        model = Reassessment
        fields = ['passing_marksheet', 'update_details']
        widgets = {
            'passing_marksheet': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.pdf,.jpg,.jpeg,.png',
                'required': True
            }),
            'update_details': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Any additional details about clearing the backlog'
            }),
        }
        labels = {
            'passing_marksheet': 'Passing Marksheet (PDF/Image)',
            'update_details': 'Additional Details',
        }


# ======================== STUDENT MANAGEMENT FORM ========================
class StudentForm(forms.ModelForm):
    """Form for creating and editing student information"""
    class Meta:
        model = Student
        fields = ['name', 'roll_no', 'department', 'email', 'marks', 'has_backlog', 'backlog_count', 'academic_status']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Student Full Name',
                'required': True
            }),
            'roll_no': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., 2021CSE001',
                'required': True
            }),
            'department': forms.Select(attrs={
                'class': 'form-select',
                'required': True
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'student@example.com',
                'required': True
            }),
            'marks': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '850',
                'step': '0.01',
                'min': '0',
                'required': True
            }),
            'has_backlog': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'backlog_count': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '0',
                'min': '0',
                'required': True
            }),
            'academic_status': forms.Select(attrs={
                'class': 'form-select',
                'required': True
            }),
        }
        labels = {
            'name': 'Student Name',
            'roll_no': 'Roll Number',
            'department': 'Department',
            'email': 'Email Address',
            'marks': 'Grand Total Marks',
            'has_backlog': 'Has Backlog?',
            'backlog_count': 'Number of Backlogs',
            'academic_status': 'Academic Status',
        }


# ======================== STUDENT RESULTS FORM ========================
class StudentResultsForm(forms.ModelForm):
    class Meta:
        model = StudentResults
        fields = [
            'sr_no', 'roll_no', 'student_name',
            'dbms_th', 'dbms_int', 'dbms_total',
            'toc_th', 'toc_int', 'toc_total',
            'se_th', 'se_int', 'se_total',
            'dss_th', 'dss_int', 'dss_total',
            'dm_th', 'dm_int', 'dm_total',
            'st_th', 'st_int', 'st_total',
            'grand_total', 'result'
        ]
        widgets = {
            'sr_no': forms.NumberInput(attrs={'class': 'form-control'}),
            'roll_no': forms.TextInput(attrs={'class': 'form-control', 'readonly': True}),
            'student_name': forms.TextInput(attrs={'class': 'form-control', 'readonly': True}),
            
            'dbms_th': forms.NumberInput(attrs={'class': 'form-control'}),
            'dbms_int': forms.NumberInput(attrs={'class': 'form-control'}),
            'dbms_total': forms.NumberInput(attrs={'class': 'form-control'}),
            
            'toc_th': forms.NumberInput(attrs={'class': 'form-control'}),
            'toc_int': forms.NumberInput(attrs={'class': 'form-control'}),
            'toc_total': forms.NumberInput(attrs={'class': 'form-control'}),
            
            'se_th': forms.NumberInput(attrs={'class': 'form-control'}),
            'se_int': forms.NumberInput(attrs={'class': 'form-control'}),
            'se_total': forms.NumberInput(attrs={'class': 'form-control'}),
            
            'dss_th': forms.NumberInput(attrs={'class': 'form-control'}),
            'dss_int': forms.NumberInput(attrs={'class': 'form-control'}),
            'dss_total': forms.NumberInput(attrs={'class': 'form-control'}),
            
            'dm_th': forms.NumberInput(attrs={'class': 'form-control'}),
            'dm_int': forms.NumberInput(attrs={'class': 'form-control'}),
            'dm_total': forms.NumberInput(attrs={'class': 'form-control'}),
            
            'st_th': forms.NumberInput(attrs={'class': 'form-control'}),
            'st_int': forms.NumberInput(attrs={'class': 'form-control'}),
            'st_total': forms.NumberInput(attrs={'class': 'form-control'}),
            
            'grand_total': forms.NumberInput(attrs={'class': 'form-control', 'readonly': True}),
            'result': forms.Select(attrs={'class': 'form-control'}),
        }


class ImportStudentResultsForm(forms.Form):
    """Form for importing student results from Excel"""
    excel_file = forms.FileField(
        label='Upload Excel File (.xlsx or .xls)',
        help_text='File should have columns: Roll No, Name, and subject marks (DBMS, TOC, SE, DSS, DM, S&T)',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.xlsx,.xls'
        })
    )