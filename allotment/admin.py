from django.contrib import admin
from .models import PreferenceWindow
from .models import (
    Student, MinorBranch, OpenElective,
    MinorPreference, DoubleMinorPreference, OEPreference,
    MinorAllocation, DoubleMinorAllocation, OEAllocation,
    EligibilityRule, OEEligibilityRule,
    # New models
    Notification, PreferenceSnapshot, WaitlistEntry,
    AuditLog, StudentFeedback, Announcement, AllocationStatistics,
    AllocationSnapshot, UserPreferences,
    Reassessment, ReassessmentWindow,  # Added
    # Teacher requirement models
    PreferenceSubmission, AbscondingStudent
)


# ======================== EXISTING MODELS ========================

# ENHANCED STUDENT ADMIN - Full CRUD Operations
@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    """
    Comprehensive admin interface for managing students.
    Allows Create, Read, Update, Delete operations on student records.
    """
    list_display = [
        'roll_no', 'name', 'department', 'marks', 'percentage', 
        'is_validated', 'has_backlog', 'backlog_count', 'academic_status'
    ]
    
    list_filter = [
        'department', 'academic_status', 'has_backlog', 'is_validated', 'percentage'
    ]
    
    search_fields = [
        'roll_no', 'name', 'user__username', 'user__email', 'email'
    ]
    
    readonly_fields = ['user', 'email']
    
    fieldsets = (
        ('Personal Information', {
            'fields': ['user', 'roll_no', 'name', 'email', 'department']
        }),
        ('Academic Details', {
            'fields': ['marks', 'percentage', 'academic_status', 'is_validated']
        }),
        ('Backlog Information', {
            'fields': ['has_backlog', 'backlog_count', 'reassessment_applied'],
            'classes': ('collapse',)  # Collapsible section
        }),
    )
    
    actions = ['mark_has_backlog', 'mark_no_backlog', 'update_status_reassessment']
    
    def mark_has_backlog(self, request, queryset):
        """Bulk action: Mark selected students as having backlog"""
        updated = queryset.update(has_backlog=True)
        self.message_user(request, f'✅ Marked {updated} student(s) as having backlog.')
    mark_has_backlog.short_description = "Mark selected students as having backlog"
    
    def mark_no_backlog(self, request, queryset):
        """Bulk action: Mark selected students as having no backlog"""
        updated = queryset.update(has_backlog=False, backlog_count=0)
        self.message_user(request, f'✅ Marked {updated} student(s) as having no backlog.')
    mark_no_backlog.short_description = "Mark selected students as having NO backlog"
    
    def update_status_reassessment(self, request, queryset):
        """Bulk action: Update status to REASSESSMENT_PENDING"""
        updated = queryset.update(academic_status='REASSESSMENT_PENDING')
        self.message_user(request, f'✅ Updated {updated} student(s) status to REASSESSMENT_PENDING.')
    update_status_reassessment.short_description = "Update status to REASSESSMENT_PENDING"
    
    def get_readonly_fields(self, request, obj=None):
        """Make user and email fields read-only for existing students"""
        if obj:  # Editing an existing object
            return self.readonly_fields + ['email']
        return self.readonly_fields


admin.site.register(MinorBranch)
admin.site.register(OpenElective)
admin.site.register(MinorPreference)
admin.site.register(DoubleMinorPreference)
admin.site.register(OEPreference)
admin.site.register(MinorAllocation)
admin.site.register(DoubleMinorAllocation)
admin.site.register(OEAllocation)
admin.site.register(PreferenceWindow)


# ======================== ELIGIBILITY RULES (ADMIN-EDITABLE) ========================

@admin.register(EligibilityRule)
class EligibilityRuleAdmin(admin.ModelAdmin):
    """
    🔒 READ-ONLY: Minor Branch eligibility rules (PROTECTED).
    Admins can VIEW but CANNOT create, edit, or delete.
    """
    list_display = ['branch', 'rule_type', 'value', 'is_active', 'created_at']
    list_filter = ['branch', 'rule_type', 'is_active', 'created_at']
    search_fields = ['branch__name']
    readonly_fields = ['branch', 'rule_type', 'value', 'is_active', 'created_at', 'updated_at']
    
    def has_add_permission(self, request):
        return False  # Cannot add rules
    
    def has_delete_permission(self, request, obj=None):
        return False  # Cannot delete rules
    
    def has_change_permission(self, request, obj=None):
        return True  # Can view only (all fields readonly)


@admin.register(OEEligibilityRule)
class OEEligibilityRuleAdmin(admin.ModelAdmin):
    """
    🔒 READ-ONLY: Open Elective eligibility rules (PROTECTED).
    Admins can VIEW but CANNOT create, edit, or delete.
    """
    list_display = ['oe_subject', 'rule_type', 'value', 'is_active', 'created_at']
    list_filter = ['oe_subject', 'rule_type', 'is_active', 'created_at']
    search_fields = ['oe_subject__name']
    readonly_fields = ['oe_subject', 'rule_type', 'value', 'is_active', 'created_at', 'updated_at']
    
    def has_add_permission(self, request):
        return False  # Cannot add rules
    
    def has_delete_permission(self, request, obj=None):
        return False  # Cannot delete rules
    
    def has_change_permission(self, request, obj=None):
        return True  # Can view only (all fields readonly)


# ======================== NEW MODEL ADMINS ========================

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['student', 'title', 'notification_type', 'is_read', 'created_at']
    list_filter = ['notification_type', 'is_read', 'created_at']
    search_fields = ['student__name', 'title', 'message']
    readonly_fields = ['created_at']


@admin.register(PreferenceSnapshot)
class PreferenceSnapshotAdmin(admin.ModelAdmin):
    list_display = ['student', 'version', 'created_at']
    list_filter = ['created_at']
    search_fields = ['student__name']
    readonly_fields = ['created_at']


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    list_display = ['student', 'position', 'allocated', 'requested_at']
    list_filter = ['allocated', 'requested_at']
    search_fields = ['student__name']
    readonly_fields = ['requested_at']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'action', 'timestamp', 'ip_address']
    list_filter = ['action', 'timestamp']
    search_fields = ['user__username', 'description', 'ip_address']
    readonly_fields = ['timestamp']


@admin.register(StudentFeedback)
class StudentFeedbackAdmin(admin.ModelAdmin):
    list_display = ['student', 'rating', 'satisfaction_with_allocation', 'submitted_at']
    list_filter = ['rating', 'satisfaction_with_allocation', 'submitted_at']
    search_fields = ['student__name', 'comments']
    readonly_fields = ['submitted_at']


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ['title', 'is_active', 'created_by', 'created_at', 'expires_at']
    list_filter = ['is_active', 'created_at', 'expires_at']
    search_fields = ['title', 'content']
    readonly_fields = ['created_at']


@admin.register(AllocationStatistics)
class AllocationStatisticsAdmin(admin.ModelAdmin):
    list_display = ['department', 'total_students', 'total_allocated', 'allocation_percentage', 'created_at']
    list_filter = ['department', 'created_at']
    readonly_fields = ['created_at']


@admin.register(AllocationSnapshot)
class AllocationSnapshotAdmin(admin.ModelAdmin):
    list_display = ['created_by', 'description', 'created_at']
    list_filter = ['created_at']
    search_fields = ['created_by__username', 'description']
    readonly_fields = ['created_at']


@admin.register(UserPreferences)
class UserPreferencesAdmin(admin.ModelAdmin):
    list_display = ['user', 'theme', 'receive_email_notifications', 'receive_sms_notifications']
    list_filter = ['theme', 'receive_email_notifications', 'receive_sms_notifications']
    search_fields = ['user__username']


# ✅ REASSESSMENT MODELS
@admin.register(ReassessmentWindow)
class ReassessmentWindowAdmin(admin.ModelAdmin):
    list_display = ['current_phase', 'is_open', 'created_at']
    list_filter = ['current_phase', 'is_open', 'created_at']
    fields = ['current_phase', 'is_open', 'description']


@admin.register(Reassessment)
class ReassessmentAdmin(admin.ModelAdmin):
    list_display = ['student', 'status', 'declared_at', 'results_submitted_at', 'approved_at']
    list_filter = ['status', 'declared_at', 'results_submitted_at']
    search_fields = ['student__name', 'student__roll_no', 'backlog_subjects']
    readonly_fields = ['declared_at', 'updated_at', 'approved_at', 'reviewed_by']
    fieldsets = (
        ('Student Info', {'fields': ['student']}),
        ('Phase 1: Backlog Declaration', {'fields': ['has_backlog', 'backlog_subjects', 'backlog_details', 'fail_marksheet', 'declared_at']}),
        ('Phase 2: Results Update', {'fields': ['passing_marksheet', 'update_details', 'results_submitted_at']}),
        ('Admin Review', {'fields': ['status', 'reviewed_by', 'admin_remarks', 'approved_at']}),
    )


# ======================== TEACHER REQUIREMENT MODELS ========================
# StudentImport and ImportedStudent removed from admin UI

@admin.register(PreferenceSubmission)
class PreferenceSubmissionAdmin(admin.ModelAdmin):
    """Admin interface for preference submission timestamps"""
    list_display = ['student_name', 'roll_number', 'grand_total_marks', 'submitted_at']
    list_filter = ['submitted_at']
    search_fields = ['student__name', 'student__roll_no']
    readonly_fields = ['student', 'submitted_at', 'student_marks']
    
    fieldsets = (
        ('Student Information', {
            'fields': ['student', 'student_marks']
        }),
        ('Submission Details', {
            'fields': ['submitted_at']
        }),
    )
    
    def student_name(self, obj):
        """Display student name"""
        return obj.student.name
    student_name.short_description = 'Student Name'
    student_name.admin_order_field = 'student__name'
    
    def roll_number(self, obj):
        """Display student roll number"""
        return obj.student.roll_no
    roll_number.short_description = 'Roll No'
    roll_number.admin_order_field = 'student__roll_no'
    
    def grand_total_marks(self, obj):
        """Display grand total marks (imported from Excel)"""
        marks = obj.student.marks
        return f"{marks} marks"
    grand_total_marks.short_description = 'Grand Total Marks'
    grand_total_marks.admin_order_field = 'student__marks'
    
    def student_marks(self, obj):
        """Read-only display of student marks in fieldset"""
        return f"Grand Total: {obj.student.marks} marks"
    student_marks.short_description = 'Grand Total Marks (From Excel)'


@admin.register(AbscondingStudent)
class AbscondingStudentAdmin(admin.ModelAdmin):
    """Admin interface for absconding students"""
    list_display = ['imported_student', 'identified_at', 'auto_allocated']
    list_filter = ['auto_allocated', 'identified_at']
    search_fields = ['imported_student__full_name', 'imported_student__roll_no']
    readonly_fields = ['identified_at']
    
    fieldsets = (
        ('Student Information', {
            'fields': ['imported_student']
        }),
        ('Allocation Status', {
            'fields': ['auto_allocated', 'identified_at']
        }),
    )
    
    actions = ['run_auto_allocation']
    
    def run_auto_allocation(self, request, queryset):
        """Bulk action: Run auto-allocation for selected absconding students"""
        from .utils import run_absconding_allocation
        try:
            count = run_absconding_allocation()
            self.message_user(request, f'✅ Auto-allocated {count} absconding student(s).')
        except Exception as e:
            self.message_user(request, f'❌ Error during allocation: {str(e)}', level='error')
    run_auto_allocation.short_description = "Run auto-allocation for selected students"

