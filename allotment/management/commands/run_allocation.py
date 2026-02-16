"""
Django management command to run the allocation engine.

Usage:
    python manage.py run_allocation --type minor
    python manage.py run_allocation --type oe
    python manage.py run_allocation --type both
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
import logging

from allotment.allocation_engine import AllocationEngine, run_minor_allocation, run_oe_allocation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Run the rank-based deterministic allocation engine for students'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--type',
            type=str,
            default='minor',
            choices=['minor', 'oe', 'both'],
            help='Type of allocation to run (default: minor)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be allocated without making changes'
        )
    
    def handle(self, *args, **options):
        allocation_type = options['type']
        dry_run = options.get('dry_run', False)
        
        self.stdout.write(self.style.SUCCESS(
            f"\n{'='*80}\nSTARTING ALLOCATION ENGINE\n{'='*80}\n"
        ))
        
        if allocation_type == 'both':
            self._run_minor_allocation()
            self.stdout.write("\n")
            self._run_oe_allocation()
        elif allocation_type == 'minor':
            self._run_minor_allocation()
        elif allocation_type == 'oe':
            self._run_oe_allocation()
        
        self.stdout.write(self.style.SUCCESS(
            f"\n{'='*80}\nALLOCATION ENGINE COMPLETE\n{'='*80}\n"
        ))
    
    def _run_minor_allocation(self):
        """Run minor branch allocation."""
        self.stdout.write(self.style.WARNING("Running MINOR BRANCH allocation...\n"))
        
        try:
            engine = AllocationEngine(allocation_type='minor')
            results = engine.run_allocation()
            self._display_results(results, 'MINOR')
        except Exception as e:
            raise CommandError(f"Minor allocation failed: {e}")
    
    def _run_oe_allocation(self):
        """Run OE allocation."""
        self.stdout.write(self.style.WARNING("Running OPEN ELECTIVE allocation...\n"))
        
        try:
            engine = AllocationEngine(allocation_type='oe')
            results = engine.run_allocation()
            self._display_results(results, 'OE')
        except Exception as e:
            raise CommandError(f"OE allocation failed: {e}")
    
    def _display_results(self, results, allocation_type):
        """Display allocation results."""
        self.stdout.write(self.style.SUCCESS(f"\n{allocation_type} ALLOCATION RESULTS:\n"))
        self.stdout.write(f"  Total Processed: {results['total_processed']}")
        self.stdout.write(self.style.SUCCESS(f"  ✓ Allocated: {results['total_allocated']}"))
        self.stdout.write(self.style.ERROR(f"  ✗ Not Allocated: {results['total_not_allocated']}"))
        self.stdout.write(self.style.WARNING(f"  ⏳ Waitlisted: {results['total_waitlisted']}"))
        self.stdout.write(f"  Duration: {results['duration_seconds']:.2f}s\n")
