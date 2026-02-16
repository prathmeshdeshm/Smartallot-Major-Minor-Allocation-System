"""
Management command to clear all eligibility rules so admins can redefine them.
"""
from django.core.management.base import BaseCommand
from allotment.models import EligibilityRule, OEEligibilityRule


class Command(BaseCommand):
    help = 'Delete all EligibilityRule and OEEligibilityRule records'

    def handle(self, *args, **options):
        minor_count = EligibilityRule.objects.count()
        oe_count = OEEligibilityRule.objects.count()

        EligibilityRule.objects.all().delete()
        OEEligibilityRule.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(
            f'\n✅ Cleared {minor_count} Minor rules and {oe_count} OE rules.\n'
            'Admins can now define rules from the admin panel.\n'
        ))
