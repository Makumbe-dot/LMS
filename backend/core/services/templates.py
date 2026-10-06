"""The wording of every message the system sends a borrower.

Each kind has built-in wording and a fixed set of placeholders. An administrator
may replace the wording in Settings; a blank template falls back to the built-in
one, so clearing a box restores the default. A placeholder outside the kind's set
is refused when the template is saved, never discovered when a message goes out.
"""
from string import Formatter

from ..exceptions import BusinessRuleError
from ..models import OrganisationSetting

COMMON = {
    "first_name": "The borrower's first name",
    "last_name": "The borrower's surname",
    "institution": "The institution's name",
    "institution_phone": "The institution's phone number",
}

TEMPLATES = {
    "reminder": {
        "label": "Instalment reminder",
        "default": ("Dear {first_name}, instalment {number} of {amount} on loan {loan_no} is "
                    "due on {due_date}. Thank you."),
        "placeholders": {**COMMON, "loan_no": "The loan number", "number": "Instalment number",
                         "amount": "What is due, with currency", "due_date": "The due date"},
    },
    "arrears": {
        "label": "Arrears notice",
        "default": ("Dear {first_name}, loan {loan_no} is {amount} in arrears ({days} days). "
                    "Please pay to avoid further penalties."),
        "placeholders": {**COMMON, "loan_no": "The loan number",
                         "amount": "The amount overdue, with currency",
                         "days": "Days past due"},
    },
    "receipt": {
        "label": "Repayment receipt",
        "default": ("Dear {first_name}, we have received {amount} on loan {loan_no}. "
                    "Balance {balance}. Thank you."),
        "placeholders": {**COMMON, "loan_no": "The loan number",
                         "amount": "The amount received, with currency",
                         "balance": "What is still owed, with currency"},
    },
    "promise": {
        "label": "Promise-to-pay reminder",
        "default": ("Dear {first_name}, as agreed, please pay {amount} on loan {loan_no} by "
                    "{promised_date}. Thank you."),
        "placeholders": {**COMMON, "loan_no": "The loan number",
                         "amount": "The amount promised, with currency",
                         "promised_date": "The date promised"},
    },
    "signing_code": {
        "label": "Agreement signing code",
        "default": ("{institution}: your code to sign the agreement for loan {loan_no} is "
                    "{code}. It expires in {minutes} minutes. Never share it."),
        "placeholders": {**COMMON, "loan_no": "The loan number", "code": "The one-time code",
                         "minutes": "Minutes until the code expires"},
    },
    "portal_code": {
        "label": "Borrower portal sign-in code",
        "default": ("{institution}: your sign-in code is {code}. It expires in {minutes} "
                    "minutes. Never share it."),
        "placeholders": {**COMMON, "code": "The one-time code",
                         "minutes": "Minutes until the code expires"},
    },
}


def _fields(text: str) -> set[str]:
    try:
        return {name for _, name, _, _ in Formatter().parse(text) if name is not None}
    except ValueError as exc:  # an unmatched brace
        raise BusinessRuleError(f"The template cannot be read: {exc}")


def validate(kind: str, text: str) -> str:
    if kind not in TEMPLATES:
        raise BusinessRuleError(f"Unknown message kind: {kind}")
    text = (text or "").strip()
    if not text:
        return ""
    allowed = TEMPLATES[kind]["placeholders"]
    unknown = sorted(name for name in _fields(text) if name not in allowed)
    if unknown:
        raise BusinessRuleError(
            f"{TEMPLATES[kind]['label']}: unknown placeholder "
            + ", ".join("{" + n + "}" for n in unknown)
            + ". Use " + ", ".join("{" + n + "}" for n in allowed))
    if len(text) > 480:
        raise BusinessRuleError(f"{TEMPLATES[kind]['label']}: at most 480 characters")
    return text


def catalogue() -> list[dict]:
    custom = OrganisationSetting.load().message_templates or {}
    return [{"kind": kind, "label": spec["label"], "default": spec["default"],
             "placeholders": [{"name": n, "about": a} for n, a in spec["placeholders"].items()],
             "template": custom.get(kind, "")}
            for kind, spec in TEMPLATES.items()]


def save(templates: dict) -> list[dict]:
    """Replace the given kinds' wording; a blank one goes back to the default."""
    row = OrganisationSetting.load()
    current = dict(row.message_templates or {})
    for kind, text in templates.items():
        cleaned = validate(kind, text)
        if cleaned:
            current[kind] = cleaned
        else:
            current.pop(kind, None)
    row.message_templates = current
    row.save(update_fields=["message_templates"])
    return catalogue()


def render(kind: str, borrower=None, settings_row=None, **values) -> str:
    """The message for one borrower, from the institution's wording or the default."""
    settings_row = settings_row or OrganisationSetting.load()
    text = (settings_row.message_templates or {}).get(kind) or TEMPLATES[kind]["default"]
    context = {
        "first_name": getattr(borrower, "first_name", "") or "",
        "last_name": getattr(borrower, "last_name", "") or "",
        "institution": settings_row.name or "",
        "institution_phone": settings_row.phone or "",
        **{k: "" if v is None else v for k, v in values.items()},
    }
    try:
        return text.format_map(context)
    except (KeyError, ValueError, IndexError):
        # Saved before a placeholder was withdrawn: never let wording stop a message.
        return TEMPLATES[kind]["default"].format_map(context)
