"""
Management command to display predefined eligibility rules.
AUTOMATIC RULE CREATION HAS BEEN DISABLED since predefined rules already exist.

⚠️  To add rules for new courses, use Admin Panel → Manage Courses → Create Rule

This command now only displays existing rules for reference.
All automatic rule generation methods have been disabled.
"""

from django.core.management.base import BaseCommand
from allotment.models import EligibilityRule, OEEligibilityRule


class Command(BaseCommand):
    help = 'Display predefined eligibility rules (Automatic creation DISABLED)'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING(
            '\n' + '='*70 +
            '\n⚠️  AUTOMATIC RULE CREATION: DISABLED' +
            '\n' + '='*70 +
            '\n\nSince predefined eligibility rules already exist,\n'
            'automatic rule creation is PERMANENTLY DISABLED.\n\n'
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            '📋 TO ADD RULES FOR NEW COURSES:\n'
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
            '1. Go to Admin Dashboard\n'
            '2. Click → Manage Courses\n'
            '3. Find or create your new course\n'
            '4. Click course name to edit\n'
            '5. Scroll to bottom → "Create Rule"\n'
            '6. Select rule type:\n'
            '   • DEPARTMENT_BLOCK: Block specific departments\n'
            '   • MIN_PERCENTAGE: Set minimum marks requirement\n'
            '7. Enter rule value and save\n'
            '8. Rule is now ACTIVE by default\n'
            '9. Use "Toggle" to deactivate/reactivate\n\n'
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            '📊 CURRENT SYSTEM RULES:\n'
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        ))
        
        # Display existing rules
        rules = EligibilityRule.objects.all()
        oe_rules = OEEligibilityRule.objects.all()
        
        self.stdout.write(self.style.SUCCESS(f'\n✅ MINOR BRANCH RULES ({rules.count()} total)\n'))
        if rules.count() > 0:
            for rule in rules:
                status = '✅ ACTIVE ' if rule.is_active else '❌ INACTIVE'
                rule_type = rule.get_rule_type_display()
                self.stdout.write(f'   {status} | {rule.branch.name:15} | {rule_type}')
        else:
            self.stdout.write('   (No rules configured)')
        
        self.stdout.write(self.style.SUCCESS(f'\n✅ OPEN ELECTIVE RULES ({oe_rules.count()} total)\n'))
        if oe_rules.count() > 0:
            for rule in oe_rules:
                status = '✅ ACTIVE ' if rule.is_active else '❌ INACTIVE'
                rule_type = rule.get_rule_type_display()
                self.stdout.write(f'   {status} | {rule.oe_subject.name:20} | {rule_type}')
        else:
            self.stdout.write('   (No rules configured)')
        
        self.stdout.write(self.style.SUCCESS(
            '\n' + '='*70 +
            f'\n✅ TOTAL RULES: {rules.count() + oe_rules.count()} (All Protected - Cannot be auto-created)' +
            '\n' + '='*70 +
            '\n💡 System is ready to accept new rules from Admin Panel\n'
        ))
