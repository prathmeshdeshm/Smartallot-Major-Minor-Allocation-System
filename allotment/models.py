from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _



class Student(models.Model):
    DEPARTMENTS = [
        ('CSE', 'Computer Science & Engineering'),
        ('IT', 'Information Technology'),
        ('ECE', 'Electronics & Comm. Engineering'),
        ('EEE', 'Electrical & Electronics Engineering'),
        ('MECH', 'Mechanical Engineering'),
        ('CIVIL', 'Civil Engineering'),
        ('ENTC', 'Electronics & Telecomm. Engineering'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    roll_no = models.CharField(max_length=50, unique=True)
    department = models.CharField(max_length=50, choices=DEPARTMENTS)
    percentage = models.FloatField()
    marks = models.FloatField(default=0)  # NEW: Marks field for sorting
    email = models.EmailField(unique=True)
    has_backlog = models.BooleanField(default=False)
    backlog_count = models.PositiveIntegerField(default=0)

    reassessment_applied = models.BooleanField(default=False)

    academic_status = models.CharField(
        max_length=30,
        choices=[
            ('CLEAR', 'No Backlog'),
            ('REASSESSMENT_PENDING', 'Reassessment Pending'),
            ('CLEARED_AFTER_REASSESSMENT', 'Cleared After Reassessment'),
            ('FAILED_REASSESSMENT', 'Failed Reassessment'),
        ],
        default='CLEAR'
    )
    
    is_validated = models.BooleanField(default=False)  # NEW: Student validation status
    validated_at = models.DateTimeField(null=True, blank=True)  # NEW: Validation timestamp

    def __str__(self):
        return self.name


# NEW: Student Validation Log
class StudentValidationLog(models.Model):
    """Log of student validation attempts for audit trail"""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='validation_logs')
    attempted_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=False)
    reason = models.CharField(max_length=500, blank=True, null=True)
    
    class Meta:
        ordering = ['-attempted_at']
        indexes = [
            models.Index(fields=['student', '-attempted_at']),
        ]
    
    def __str__(self):
        status = 'Success' if self.success else 'Failed'
        return f"{self.student.roll_no} - {status} - {self.attempted_at.strftime('%Y-%m-%d %H:%M:%S')}"


# NEW: Excel Import Management
class StudentImport(models.Model):
    """Track student data imported from Excel"""
    file = models.FileField(upload_to='imports/')
    imported_at = models.DateTimeField(auto_now_add=True)
    imported_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    total_records = models.IntegerField(default=0)
    successful_records = models.IntegerField(default=0)
    failed_records = models.IntegerField(default=0)
    
    class Meta:
        ordering = ['-imported_at']
    
    def __str__(self):
        return f"Import from {self.imported_at.strftime('%Y-%m-%d %H:%M')}"


class ImportedStudent(models.Model):
    """Students imported from Excel (before they create accounts)"""
    import_batch = models.ForeignKey(StudentImport, on_delete=models.CASCADE, related_name='students')
    full_name = models.CharField(max_length=100)
    roll_no = models.CharField(max_length=50)
    marks = models.FloatField()
    percentage = models.FloatField()
    major_branch = models.CharField(
        max_length=50,
        choices=Student.DEPARTMENTS + [('GENERAL', 'General / Not Specified')],
        default='GENERAL'
    )
    
    class Meta:
        unique_together = ('import_batch', 'roll_no')
        ordering = ['roll_no']
    
    def __str__(self):
        return f"{self.full_name} ({self.roll_no})"


# NEW: Preference Submission Tracking
class PreferenceSubmission(models.Model):
    """Track when students submit their preferences with server-side timestamp"""
    student = models.OneToOneField(Student, on_delete=models.CASCADE, related_name='preference_submission')
    submitted_at = models.DateTimeField(auto_now_add=True)  # Server-side, immutable
    minor_submitted_at = models.DateTimeField(null=True, blank=True)
    oe_submitted_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-submitted_at']
    
    def __str__(self):
        return f"{self.student.name} submitted preferences"


# NEW: Absconding Students Tracking
class AbscondingStudent(models.Model):
    """Students who didn't submit preferences within the deadline"""
    imported_student = models.ForeignKey(ImportedStudent, on_delete=models.CASCADE, related_name='absconding_record')
    identified_at = models.DateTimeField(auto_now_add=True)
    auto_allocated = models.BooleanField(default=False)
    allocated_minor = models.ForeignKey(
        'MinorBranch', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='absconding_allocations',
        help_text='Minor branch assigned during auto-allocation'
    )
    allocated_oe = models.ForeignKey(
        'OpenElective', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='absconding_allocations',
        help_text='Open elective assigned during auto-allocation'
    )
    
    class Meta:
        ordering = ['-identified_at']
    
    def __str__(self):
        return f"{self.imported_student.full_name} (Absconding)"


class MinorBranch(models.Model):
    name = models.CharField(max_length=100)
    capacity = models.IntegerField()
    restricted_dept = models.CharField(max_length=50, null=True, blank=True)
    
    # 🔹 NEW: which department offers this minor
    offering_dept = models.CharField(
        max_length=50,
        choices=Student.DEPARTMENTS,
        null=True,
        blank=True,
        help_text="Department that owns/offers this minor branch."
    )

    def __str__(self):
        return self.name



class OpenElective(models.Model):
    name = models.CharField(max_length=100)
    capacity = models.IntegerField()
    # 🔹 NEW: offering department for OE
    offering_dept = models.CharField(
        max_length=50,
        choices=Student.DEPARTMENTS,
        help_text="Department that offers this Open Elective."
    )

    def __str__(self):
        return self.name


class MinorPreference(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name='minor_preferences'
    )
    minor_branch = models.ForeignKey(MinorBranch, on_delete=models.CASCADE)
    priority = models.IntegerField()
    submitted_at = models.DateTimeField(auto_now=True)
    auto_generated = models.BooleanField(
        default=False,
        help_text='True if this preference was auto-generated for an absconding student'
    )
    
    class Meta:
        unique_together = ('student', 'priority')  # ✅ FIX 6: Prevent duplicate priorities
        ordering = ['student', 'priority']
    
    def clean(self):
        # ✅ FIX 6: Validate priority range
        if self.priority < 1 or self.priority > 5:
            raise ValidationError({'priority': 'Priority must be between 1 and 5'})
        
        # ✅ FIX 6: Check for duplicate branch selection
        existing = MinorPreference.objects.filter(
            student=self.student,
            minor_branch=self.minor_branch
        ).exclude(pk=self.pk)
        if existing.exists():
            raise ValidationError({'minor_branch': 'You have already selected this branch'})
        
        # NEW: Same-branch minor restriction - cannot select own major as minor
        if self.minor_branch.offering_dept == self.student.department:
            raise ValidationError({'minor_branch': 'You cannot select your own major branch as a minor'})
        
        # NEW: IT/CSE restriction - if major is IT, CSE minor not allowed; if major is CSE, IT not allowed
        if self.student.department == 'IT' and self.minor_branch.offering_dept == 'CSE':
            raise ValidationError({'minor_branch': 'IT students cannot select CSE as minor'})
        if self.student.department == 'CSE' and self.minor_branch.offering_dept == 'IT':
            raise ValidationError({'minor_branch': 'CSE students cannot select IT as minor'})
    
    def save(self, *args, **kwargs):
        # ✅ FIX 10: Enforce preference window deadline for MINOR preferences
        if not kwargs.pop('skip_window_check', False):
            if not PreferenceWindow.is_open(preference_type='minor'):
                raise ValidationError('Minor preference window is closed. Cannot save preferences.')
        self.full_clean()
        super().save(*args, **kwargs)
    

class DoubleMinorPreference(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name='double_minor_preferences'
    )
    minor_branch = models.ForeignKey(MinorBranch, on_delete=models.CASCADE)
    priority = models.IntegerField()
    submitted_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('student', 'priority')  # ✅ FIX 6: Prevent duplicate priorities
        ordering = ['student', 'priority']
    
    def clean(self):
        # ✅ FIX 6: Validate priority range
        if self.priority < 1 or self.priority > 5:
            raise ValidationError({'priority': 'Priority must be between 1 and 5'})
        
        # ✅ FIX 6: Check for duplicate branch selection within Minor 2
        existing = DoubleMinorPreference.objects.filter(
            student=self.student,
            minor_branch=self.minor_branch
        ).exclude(pk=self.pk)
        if existing.exists():
            raise ValidationError({'minor_branch': 'You have already selected this branch in Minor 2'})
        
        # NEW: Same-branch minor restriction - cannot select own major as minor
        if self.minor_branch.offering_dept == self.student.department:
            raise ValidationError({'minor_branch': 'You cannot select your own major branch as a minor'})
        
        # NEW: IT/CSE restriction - if major is IT, CSE minor not allowed; if major is CSE, IT not allowed
        if self.student.department == 'IT' and self.minor_branch.offering_dept == 'CSE':
            raise ValidationError({'minor_branch': 'IT students cannot select CSE as minor'})
        if self.student.department == 'CSE' and self.minor_branch.offering_dept == 'IT':
            raise ValidationError({'minor_branch': 'CSE students cannot select IT as minor'})
    
    def save(self, *args, **kwargs):
        #  FIX 10: Enforce preference window deadline for DOUBLE MINOR preferences
        if not kwargs.pop('skip_window_check', False):
            if not PreferenceWindow.is_open(preference_type='minor'):
                raise ValidationError('Minor preference window is closed. Cannot save preferences.')
        self.full_clean()
        super().save(*args, **kwargs)


class OEPreference(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name='oe_preferences'
    )
    oe_subject = models.ForeignKey(OpenElective, on_delete=models.CASCADE)
    priority = models.IntegerField()
    submitted_at = models.DateTimeField(auto_now=True)
    auto_generated = models.BooleanField(
        default=False,
        help_text='True if this preference was auto-generated for an absconding student'
    )
    
    class Meta:
        unique_together = ('student', 'priority')  #  FIX 6: Prevent duplicate priorities
        ordering = ['student', 'priority']
    
    def clean(self):
        #  FIX 6: Validate priority range
        if self.priority < 1 or self.priority > 5:
            raise ValidationError({'priority': 'Priority must be between 1 and 5'})
        
        #  FIX 6: Check for duplicate OE selection
        existing = OEPreference.objects.filter(
            student=self.student,
            oe_subject=self.oe_subject
        ).exclude(pk=self.pk)
        if existing.exists():
            raise ValidationError({'oe_subject': 'You have already selected this Open Elective'})
    
    def save(self, *args, **kwargs):
        #  FIX 10: Enforce preference window deadline for OE preferences
        if not kwargs.pop('skip_window_check', False):
            if not PreferenceWindow.is_open(preference_type='oe'):
                raise ValidationError('Open Elective preference window is closed. Cannot save preferences.')
        self.full_clean()
        super().save(*args, **kwargs)


class MinorAllocation(models.Model):
    # student can be null for auto-allocation of absconding students
    student = models.ForeignKey(Student, on_delete=models.CASCADE, null=True, blank=True)
    minor_branch = models.ForeignKey(MinorBranch, on_delete=models.CASCADE)
    explanation = models.TextField(blank=True, null=True)
    
    class Meta:
        unique_together = ('student', 'minor_branch')
        indexes = [
            models.Index(fields=['student']),
            models.Index(fields=['minor_branch']),
        ]
  
class DoubleMinorAllocation(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, null=True, blank=True)
    minor_branch = models.ForeignKey(MinorBranch, on_delete=models.CASCADE)
    
    class Meta:
        unique_together = ('student', 'minor_branch')
        indexes = [
            models.Index(fields=['student']),
            models.Index(fields=['minor_branch']),
        ]


class OEAllocation(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, null=True, blank=True)
    oe_subject = models.ForeignKey(OpenElective, on_delete=models.CASCADE)
    
    class Meta:
        unique_together = ('student', 'oe_subject')
        indexes = [
            models.Index(fields=['student']),
            models.Index(fields=['oe_subject']),
        ]


# -------------------- Eligibility Rules (Dynamic Engine) -------------------- #

class AbstractEligibilityRule(models.Model):
    """
    Base model for all eligibility rules (Minor + OE).
    """

    RULE_TYPES = [
        ("MIN_PERCENTAGE", "Minimum Percentage Required"),
        ("DEPARTMENT_BLOCK", "Blocked Departments"),
        # You can extend later:
        ("ALLOW_DEPARTMENTS", "Only these departments allowed"),
        ("MAX_BACKLOGS", "Maximum allowed backlogs"),
    ]

    rule_type = models.CharField(max_length=40, choices=RULE_TYPES)

    # e.g.
    #   MIN_PERCENTAGE   -> {"min_percentage": 75}
    #   DEPARTMENT_BLOCK -> {"blocked_departments": ["IT", "ECE"]}
    value = models.JSONField(
        help_text=(
            'JSON parameters, e.g. {"min_percentage": 75} or '
            '{"blocked_departments": ["IT", "ECE"]}'
        )
    )

    is_active = models.BooleanField(default=True)

    # ⬇ allow NULL so Django does NOT force a default for old rows
    created_at = models.DateTimeField(
        auto_now_add=True,
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self.get_rule_type_display()


class EligibilityRule(models.Model):
    RULE_TYPES = [
        ('MIN_PERCENTAGE', 'Minimum Percentage Required'),
        ('DEPARTMENT_BLOCK', 'Blocked Departments'),
    ]

    branch = models.ForeignKey(
        MinorBranch,
        on_delete=models.CASCADE,
        related_name='rules'
    )
    rule_type = models.CharField(max_length=20, choices=RULE_TYPES)
    value = models.JSONField()

    # 🔹 New fields
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_active', 'rule_type', 'created_at']

    def __str__(self):
        return f"Rule for {self.branch.name}: {self.get_rule_type_display()}"


class OEEligibilityRule(models.Model):
    RULE_TYPES = [
        ('MIN_PERCENTAGE', 'Minimum Percentage Required'),
        ('DEPARTMENT_BLOCK', 'Blocked Departments'),
    ]

    oe_subject = models.ForeignKey(
        OpenElective,
        on_delete=models.CASCADE,
        related_name='rules'
    )
    rule_type = models.CharField(max_length=20, choices=RULE_TYPES)
    value = models.JSONField()

    # 🔹 New fields
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_active', 'rule_type', 'created_at']

    def __str__(self):
        return f"OE Rule for {self.oe_subject.name}: {self.get_rule_type_display()}"


class PreferenceWindow(models.Model):
    """
    Controls when students can submit / edit their preferences.
    Separate windows for minor and OE allocations.
    """
    PREFERENCE_TYPES = [
        ('minor', 'Minor Branch Preferences'),
        ('oe', 'Open Elective Preferences'),
    ]
    
    preference_type = models.CharField(
        max_length=10,
        choices=PREFERENCE_TYPES,
        default='minor',
        help_text="Type of preferences this window applies to"
    )
    name = models.CharField(max_length=100)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    @classmethod
    def is_open(cls, preference_type='minor'):
        """✅ Check if preference window is currently open for given type"""
        now = timezone.now()
        return cls.objects.filter(
            preference_type=preference_type,
            is_active=True,
            start_at__lte=now,
            end_at__gte=now
        ).exists()
    
    @classmethod
    def get_open_window(cls, preference_type='minor'):
        """Get the currently open window for a preference type"""
        now = timezone.now()
        return cls.objects.filter(
            preference_type=preference_type,
            is_active=True,
            start_at__lte=now,
            end_at__gte=now
        ).first()
    
    def clean(self):
        # ✅ FIX 10: Validate window dates
        if self.end_at <= self.start_at:
            raise ValidationError({'end_at': 'End time must be after start time'})
        
        # Check for overlapping windows of same type
        overlapping = PreferenceWindow.objects.filter(
            preference_type=self.preference_type,
            is_active=True,
            start_at__lt=self.end_at,
            end_at__gt=self.start_at
        ).exclude(pk=self.pk)
        
        if overlapping.exists():
            raise ValidationError(
                {'preference_type': f'An active window already exists for {self.get_preference_type_display()}'}
            )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['preference_type', 'is_active']),
            models.Index(fields=['preference_type', 'start_at', 'end_at']),
        ]

    def __str__(self):
        return f"{self.get_preference_type_display()} - {self.name} ({self.start_at.strftime('%Y-%m-%d %H:%M')} → {self.end_at.strftime('%Y-%m-%d %H:%M')})"

    @property
    def is_open_now(self):
        now = timezone.now()
        return self.is_active and self.start_at <= now <= self.end_at



# 1️⃣ NOTIFICATIONS SYSTEM
class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('preference_window_open', 'Preference Window Opened'),
        ('preference_window_closing', 'Preference Window Closing Soon'),
        ('allocation_complete', 'Allocation Completed'),
        ('allocation_appeal_status', 'Appeal Status Updated'),
        ('system_announcement', 'System Announcement'),
    ]
    
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(max_length=50, choices=NOTIFICATION_TYPES)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.title} - {self.student.name}"
    
    def mark_as_read(self):
        self.is_read = True
        self.save()


# 2️⃣ PREFERENCE HISTORY/SNAPSHOTS
class PreferenceSnapshot(models.Model):
    """Track student's preference changes over time"""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='preference_snapshots')
    minor_preferences = models.JSONField(default=dict, help_text="Stored as JSON")
    double_minor_preferences = models.JSONField(default=dict)
    oe_preferences = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    version = models.IntegerField(default=1)
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Snapshot v{self.version} - {self.student.name} ({self.created_at})"


# 3️⃣ WAITLIST SYSTEM
class WaitlistEntry(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='waitlist_entries')
    minor_branch = models.ForeignKey(MinorBranch, on_delete=models.CASCADE, related_name='waitlist', null=True, blank=True)
    oe_subject = models.ForeignKey(OpenElective, on_delete=models.CASCADE, related_name='waitlist', null=True, blank=True)
    position = models.PositiveIntegerField()
    requested_at = models.DateTimeField(auto_now_add=True)
    allocated = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['position']
        unique_together = ('student', 'minor_branch', 'oe_subject')
    
    def __str__(self):
        return f"{self.student.name} - Position {self.position}"


# 4️ AUDIT LOGS
class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('preference_created', 'Preference Created'),
        ('preference_updated', 'Preference Updated'),
        ('preference_deleted', 'Preference Deleted'),
        ('allocation_created', 'Allocation Created'),
        ('allocation_updated', 'Allocation Updated'),
        ('allocation_run', 'Allocation Run'),  #  FIX 5: Added
        ('allocation_cleared', 'Allocation Cleared'),  #  FIX 5: Added
        ('student_created', 'Student Created'),  #  FIX 5: Added
        ('student_updated', 'Student Updated'),  #  FIX 5: Added
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('export_data', 'Data Exported'),
        ('bulk_import', 'Bulk Import'),  #  FIX 5: Added
        # Reassessment actions
        ('reassessment_approved', 'Reassessment Approved'),
        ('reassessment_rejected', 'Reassessment Rejected'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    description = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['-timestamp']),
            models.Index(fields=['user']),
            models.Index(fields=['action']),
        ]
    
    def __str__(self):
        return f"{self.get_action_display()} by {self.user} at {self.timestamp}"


# 5️ FEEDBACK/SURVEY
class StudentFeedback(models.Model):
    RATING_CHOICES = [(i, f"{i} Stars") for i in range(1, 6)]
    
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='feedback')
    rating = models.IntegerField(choices=RATING_CHOICES)
    comments = models.TextField()
    satisfaction_with_allocation = models.IntegerField(choices=RATING_CHOICES)
    difficulties_faced = models.TextField(blank=True, null=True)
    suggestions = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-submitted_at']
    
    def __str__(self):
        return f"Feedback from {self.student.name} - {self.rating} Stars"


# 6️ SYSTEM ANNOUNCEMENTS
class Announcement(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return self.title


# 7️ ALLOCATION STATISTICS
class AllocationStatistics(models.Model):
    """Store allocation metrics for reporting"""
    total_students = models.IntegerField()
    total_allocated = models.IntegerField()
    total_pending = models.IntegerField()
    department = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Stats for {self.department} - {self.created_at}"
    
    @property
    def allocation_percentage(self):
        if self.total_students == 0:
            return 0
        return (self.total_allocated / self.total_students) * 100


# 8️ ROLLBACK/VERSION CONTROL
class AllocationSnapshot(models.Model):
    """Track allocation versions for rollback"""
    minor_allocations = models.JSONField()
    double_minor_allocations = models.JSONField()
    oe_allocations = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    description = models.CharField(max_length=300, blank=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Allocation Snapshot - {self.created_at}"


# 9️ USER PREFERENCES (System Settings)
class UserPreferences(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferences')
    receive_email_notifications = models.BooleanField(default=True)
    receive_sms_notifications = models.BooleanField(default=False)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    theme = models.CharField(max_length=20, choices=[('light', 'Light'), ('dark', 'Dark')], default='light')
    
    def __str__(self):
        return f"Preferences for {self.user.username}"


#  REASSESSMENT MANAGEMENT (Two-Phase System)
class ReassessmentWindow(models.Model):
    """
    Controls reassessment phases:
    PHASE_1: Students declare backlogs and submit fail marksheets
    PHASE_2: After results, students update with passing marksheets
    """
    PHASE_CHOICES = [
        ('PHASE_1', 'Phase 1: Backlog Declaration'),
        ('PHASE_2', 'Phase 2: Results Update'),
        ('CLOSED', 'Closed'),
    ]
    
    current_phase = models.CharField(max_length=20, choices=PHASE_CHOICES, default='CLOSED')
    is_open = models.BooleanField(default=False)
    description = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Reassessment Window - {self.current_phase} (Open: {self.is_open})"
    
    @classmethod
    def get_current_window(cls):
        """Get the active reassessment window"""
        return cls.objects.filter(is_open=True).first()
    
    def can_submit_backlog(self):
        """Check if Phase 1 is open"""
        return self.is_open and self.current_phase == 'PHASE_1'
    
    def can_update_results(self):
        """Check if Phase 2 is open"""
        return self.is_open and self.current_phase == 'PHASE_2'


class Reassessment(models.Model):
    STATUS_CHOICES = [
        ('DECLARED', 'Backlog Declared (Awaiting Results)'),
        ('UNDER_REVIEW', 'Under Review'),
        ('UPDATE_SUBMITTED', 'Update Submitted (Awaiting Approval)'),
        ('APPROVED', 'Approved - Eligible for Allocation'),
        ('REJECTED', 'Rejected - Ineligible'),
    ]
    
    # Phase 1: Backlog Declaration
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='reassessments')
    has_backlog = models.BooleanField(default=False, help_text="Student confirms having backlog")
    backlog_subjects = models.TextField(help_text="Comma-separated subject codes/names with backlogs")
    backlog_details = models.TextField(help_text="Detailed information about backlogs")
    fail_marksheet = models.FileField(upload_to='reassessment/fail_marksheets/', help_text="Upload fail marksheet as proof")
    
    # Phase 2: Results Update (optional, filled after results)
    passing_marksheet = models.FileField(upload_to='reassessment/passing_marksheets/', null=True, blank=True, help_text="Upload passing marksheet after clearing")
    update_details = models.TextField(null=True, blank=True, help_text="Additional details about clearing")
    
    # Status tracking
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='DECLARED')
    
    # Timestamps
    declared_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    results_submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    
    # Admin review
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reassessment_reviews')
    admin_remarks = models.TextField(null=True, blank=True, help_text="Admin remarks on reassessment")
    
    class Meta:
        ordering = ['-declared_at']
        unique_together = ('student', 'declared_at')
        verbose_name_plural = "Reassessments"
    
    def __str__(self):
        return f"Reassessment - {self.student.roll_no} ({self.status})"
    
    def approve(self, admin_user, remarks=""):
        """Admin approves reassessment - student becomes eligible"""
        self.status = 'APPROVED'
        self.reviewed_by = admin_user
        self.approved_at = timezone.now()
        self.admin_remarks = remarks
        self.save()
        
        # Update student academic status
        self.student.academic_status = 'CLEARED_AFTER_REASSESSMENT'
        self.student.has_backlog = False
        self.student.backlog_count = 0
        self.student.save()
        
        AuditLog.objects.create(
            user=admin_user,
            action='reassessment_approved',
            description=f'Reassessment approved for {self.student.roll_no}'
        )
    
    def reject(self, admin_user, remarks=""):
        """Admin rejects reassessment - student stays ineligible"""
        self.status = 'REJECTED'
        self.reviewed_by = admin_user
        self.approved_at = timezone.now()
        self.admin_remarks = remarks
        self.save()
        
        # Update student academic status
        self.student.academic_status = 'FAILED_REASSESSMENT'
        self.student.save()
        
        AuditLog.objects.create(
            user=admin_user,
            action='reassessment_rejected',
            description=f'Reassessment rejected for {self.student.roll_no}'
        )


class StudentResults(models.Model):
    """Store detailed subject-wise exam results"""
    
    RESULT_CHOICES = [
        ('PASS', 'PASS'),
        ('FAIL', 'FAIL'),
    ]
    
    sr_no = models.IntegerField(null=True, blank=True)
    roll_no = models.CharField(max_length=20, unique=True, db_index=True)
    student_name = models.CharField(max_length=150)
    
    # DBMS (16470)
    dbms_th = models.IntegerField(null=True, blank=True)
    dbms_int = models.IntegerField(null=True, blank=True)
    dbms_total = models.IntegerField(null=True, blank=True)
    
    # TOC (16471)
    toc_th = models.IntegerField(null=True, blank=True)
    toc_int = models.IntegerField(null=True, blank=True)
    toc_total = models.IntegerField(null=True, blank=True)
    
    # SE (16472)
    se_th = models.IntegerField(null=True, blank=True)
    se_int = models.IntegerField(null=True, blank=True)
    se_total = models.IntegerField(null=True, blank=True)
    
    # DM (16409) - Changed to dm_* fields
    dm_th = models.IntegerField(null=True, blank=True)
    dm_int = models.IntegerField(null=True, blank=True)
    dm_total = models.IntegerField(null=True, blank=True)
    
    # DSS (16474) - Added this subject
    dss_th = models.IntegerField(null=True, blank=True)
    dss_int = models.IntegerField(null=True, blank=True)
    dss_total = models.IntegerField(null=True, blank=True)
    
    # S & T (16525)
    st_th = models.IntegerField(null=True, blank=True)
    st_int = models.IntegerField(null=True, blank=True)
    st_total = models.IntegerField(null=True, blank=True)
    
    # Overall
    grand_total = models.IntegerField(null=True, blank=True)
    result = models.CharField(max_length=10, choices=RESULT_CHOICES, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['roll_no']
        verbose_name_plural = "Student Results"
    
    def __str__(self):
        return f"{self.student_name} ({self.roll_no}) - Total: {self.grand_total}"
    
    def calculate_grand_total(self):
        """Calculate grand total from all subject totals"""
        totals = [
            self.dbms_total or 0,
            self.toc_total or 0,
            self.se_total or 0,
            self.dss_total or 0,
            self.dm_total or 0,
            self.st_total or 0,
        ]
        return sum(totals)
    
    def save(self, *args, **kwargs):
        # Auto-calculate grand total if not set
        if not self.grand_total:
            self.grand_total = self.calculate_grand_total()
        super().save(*args, **kwargs)
