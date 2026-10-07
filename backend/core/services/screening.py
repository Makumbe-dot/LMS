"""Sanctions and watch-list screening of borrowers (AML).

Lists are uploaded by an administrator: the UN Security Council consolidated list
in its published XML, or any list as a CSV. Every borrower is screened when they
are created or their name, ID or date of birth changes, and the whole book is
screened again whenever a list is loaded.

Matching is deliberately generous - a review queue that sees a few false alarms is
cheaper than a missed match. Names are compared ignoring accents, case,
punctuation and word order; an ID number that matches counts as a match on its own;
a different year of birth lowers the score. A hit is reviewed by a person: cleared
("not the same person") or confirmed. While a hit is unreviewed, or once it is
confirmed, the borrower's loans cannot be approved or paid out.
"""
import csv
import io
import re
import unicodedata
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

from django.db import transaction
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import Borrower, ScreeningHit, ScreeningStatus, WatchlistEntry

THRESHOLD = 85          # a score at or above this is a hit to review
TOKEN_ALIKE = 0.85      # two words this alike count as the same word ("Mohamed" / "Muhammad" do not)


def normalise(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(sorted(token for token in text.split() if len(token) > 1))


def _id_key(value) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def _year(value) -> str | None:
    found = re.search(r"(19|20)\d{2}", str(value or ""))
    return found.group(0) if found else None


def _tokens_match(mine: list[str], theirs: list[str]) -> float:
    """The share of the shorter name's words found in the longer one."""
    if not mine or not theirs:
        return 0.0
    short, long = (mine, theirs) if len(mine) <= len(theirs) else (theirs, mine)
    if len(short) < 2:
        return 0.0  # one word is never enough to call two people the same
    found = sum(1 for word in short
                if any(SequenceMatcher(None, word, other).ratio() >= TOKEN_ALIKE for other in long))
    return found / len(short)


def score(borrower_key: str, entry_key: str) -> int:
    """0-100: how alike two normalised names are."""
    if not borrower_key or not entry_key:
        return 0
    whole = SequenceMatcher(None, borrower_key, entry_key).ratio()
    mine, theirs = borrower_key.split(), entry_key.split()
    overlap = _tokens_match(mine, theirs)
    if len(mine) != len(theirs):
        overlap *= 0.95  # all of one name inside a longer one: close, not identical
    return round(100 * max(whole, overlap))


def _assess(borrower, borrower_key: str, entry: WatchlistEntry) -> tuple[int, str] | None:
    if entry.id_number and _id_key(entry.id_number) == _id_key(borrower.national_id):
        return 100, "ID number matches"
    best = max((score(borrower_key, key) for key in entry.keys.split("\n") if key), default=0)
    if best < THRESHOLD - 15:
        return None
    reason = "Name is similar"
    listed_year, own_year = _year(entry.date_of_birth), _year(borrower.date_of_birth)
    if listed_year and own_year:
        if listed_year == own_year:
            best, reason = min(100, best + 5), "Name is similar and the year of birth matches"
        else:
            best, reason = best - 15, "Name is similar but the year of birth differs"
    return (best, reason) if best >= THRESHOLD else None


def screen(borrower: Borrower, entries=None) -> list[ScreeningHit]:
    """Screen one borrower; records new hits, keeps reviewed ones as they are."""
    entries = list(entries if entries is not None else WatchlistEntry.objects.all())
    key = normalise(f"{borrower.first_name} {borrower.last_name}")
    made = []
    for entry in entries:
        found = _assess(borrower, key, entry)
        if not found:
            continue
        points, reason = found
        hit, created = ScreeningHit.objects.get_or_create(
            borrower=borrower, source=entry.source, listed_name=entry.name[:255],
            defaults={"entry": entry, "reference": entry.reference[:80], "score": points,
                      "reason": reason})
        if created:
            made.append(hit)
        elif hit.status == ScreeningStatus.OPEN:
            hit.entry, hit.score, hit.reason = entry, points, reason
            hit.save(update_fields=["entry", "score", "reason"])
    Borrower.objects.filter(pk=borrower.pk).update(screened_at=timezone.now())
    return made


def screen_everyone() -> dict:
    entries = list(WatchlistEntry.objects.all())
    found = 0
    borrowers = 0
    for borrower in Borrower.objects.all().iterator():
        found += len(screen(borrower, entries))
        borrowers += 1
    return {"borrowers": borrowers, "entries": len(entries), "new_hits": found}


def screen_if_lists(borrower: Borrower) -> None:
    """After a borrower is saved: screen them, if any list has been loaded."""
    if WatchlistEntry.objects.exists():
        screen(borrower)


# ---------------------------------------------------------------- the block
def assert_clear(borrower: Borrower, doing: str) -> None:
    """Refuse to approve or pay out while a hit is unreviewed or confirmed."""
    hits = ScreeningHit.objects.filter(borrower=borrower).exclude(status=ScreeningStatus.CLEARED)
    confirmed = hits.filter(status=ScreeningStatus.CONFIRMED).first()
    if confirmed:
        raise BusinessRuleError(
            f"Cannot {doing}: {borrower.full_name} is a confirmed match for "
            f"{confirmed.listed_name} on the {confirmed.source} list")
    if hits.exists():
        raise BusinessRuleError(
            f"Cannot {doing} yet: {borrower.full_name} resembles a name on a sanctions list. "
            "Review it under Customers > Screening first")


@transaction.atomic
def review(hit: ScreeningHit, user, decision: str, note: str) -> ScreeningHit:
    if decision not in (ScreeningStatus.CLEARED, ScreeningStatus.CONFIRMED):
        raise BusinessRuleError("Decide: cleared or confirmed")
    if not (note or "").strip():
        raise BusinessRuleError("Say why: what you checked")
    hit.status = decision
    hit.review_note = note.strip()
    hit.reviewed_by = user
    hit.reviewed_at = timezone.now()
    hit.save()
    if decision == ScreeningStatus.CONFIRMED:
        Borrower.objects.filter(pk=hit.borrower_id).update(is_blacklisted=True)
    return hit


# ---------------------------------------------------------------- loading lists
def _entry(source, name, aliases=(), dob="", id_number="", reference="", notes=""):
    names = [name, *[a for a in aliases if a]]
    return WatchlistEntry(
        source=source[:40], name=name[:255], aliases="\n".join(a for a in aliases if a),
        date_of_birth=str(dob or "")[:40], id_number=str(id_number or "")[:80],
        reference=str(reference or "")[:80], notes=str(notes or "")[:2000],
        keys="\n".join(sorted({normalise(n) for n in names if normalise(n)})))


def _text(node, path) -> str:
    found = node.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_un_xml(raw: bytes) -> list[WatchlistEntry]:
    """The UN Security Council consolidated list, as published in XML."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise BusinessRuleError(f"That is not the UN list's XML: {exc}")
    entries = []
    for person in root.iter("INDIVIDUAL"):
        name = " ".join(part for part in (_text(person, tag) for tag in
                                          ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME"))
                        if part)
        if not name:
            continue
        aliases = [_text(a, "ALIAS_NAME") for a in person.iter("INDIVIDUAL_ALIAS")]
        born = person.find("INDIVIDUAL_DATE_OF_BIRTH")
        dob = (_text(born, "DATE") or _text(born, "YEAR")) if born is not None else ""
        document = person.find("INDIVIDUAL_DOCUMENT")
        entries.append(_entry("UN", name, aliases, dob,
                              _text(document, "NUMBER") if document is not None else "",
                              _text(person, "REFERENCE_NUMBER"), _text(person, "COMMENTS1")))
    for body in root.iter("ENTITY"):
        name = _text(body, "FIRST_NAME")
        if name:
            aliases = [_text(a, "ALIAS_NAME") for a in body.iter("ENTITY_ALIAS")]
            entries.append(_entry("UN", name, aliases, "", "", _text(body, "REFERENCE_NUMBER"),
                                  _text(body, "COMMENTS1")))
    if not entries:
        raise BusinessRuleError("No names found: is this the UN consolidated list in XML?")
    return entries


def parse_csv(raw: bytes, source: str) -> list[WatchlistEntry]:
    """Any list as a CSV with a `name` column; optional `aliases` (separated by ;),
    `date_of_birth`, `id_number`, `reference` and `notes`."""
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    columns = {c.strip().lower(): c for c in (reader.fieldnames or [])}
    if "name" not in columns:
        raise BusinessRuleError("The CSV needs a 'name' column")
    get = lambda row, key: (row.get(columns[key]) or "").strip() if key in columns else ""  # noqa: E731
    entries = [_entry(source, get(row, "name"), get(row, "aliases").split(";"),
                      get(row, "date_of_birth"), get(row, "id_number"), get(row, "reference"),
                      get(row, "notes"))
               for row in reader if get(row, "name")]
    if not entries:
        raise BusinessRuleError("The CSV has no names in it")
    return entries


@transaction.atomic
def load(entries: list[WatchlistEntry]) -> dict:
    """Replace one list's entries, then screen every borrower against all lists."""
    sources = {e.source for e in entries}
    WatchlistEntry.objects.filter(source__in=sources).delete()
    WatchlistEntry.objects.bulk_create(entries, batch_size=200)
    result = screen_everyone()
    return {"loaded": len(entries), "lists": sorted(sources), **result}


def lists() -> list[dict]:
    from django.db.models import Count, Max

    return list(WatchlistEntry.objects.values("source")
                .annotate(names=Count("id"), loaded_at=Max("loaded_at")).order_by("source"))
