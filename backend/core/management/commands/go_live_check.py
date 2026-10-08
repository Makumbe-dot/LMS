"""Print the go-live checklist; exit 1 while anything fails.

    python manage.py go_live_check
"""
from django.core.management.base import BaseCommand

from core.services import golive

MARK = {"pass": "OK  ", "warn": "WARN", "fail": "FAIL"}


class Command(BaseCommand):
    help = "Is this installation ready for real borrowers? Exit 1 while any check fails."

    def handle(self, *args, **options):
        result = golive.summary(golive.checks())
        area = None
        for item in result["items"]:
            if item["area"] != area:
                area = item["area"]
                self.stdout.write(f"\n{area}")
            self.stdout.write(f"  [{MARK[item['status']]}] {item['label']}: {item['detail']}")
            if item["status"] != "pass" and item["fix"]:
                self.stdout.write(f"         -> {item['fix']}")
        self.stdout.write(f"\n{result['pass']} passed, {result['warn']} to look at, "
                          f"{result['fail']} to fix.")
        if not result["ready"]:
            raise SystemExit(1)
