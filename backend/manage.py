#!/usr/bin/env python
"""Django command-line utility for the Loan Management System."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lms_backend.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Could not import Django. Is it installed and is the virtual environment active?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
