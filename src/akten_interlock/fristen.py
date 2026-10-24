"""Fristenwächter für Teilhabeanträge nach §§ 14, 18 SGB IX (Stand Oktober 2026).

Fristberechnung nach § 26 Abs. 1 SGB X i. V. m. §§ 187 Abs. 1, 188 Abs. 2 und 3 BGB:
der Eingangstag zählt nicht; eine Wochenfrist endet am gleichnamigen Wochentag,
eine Monatsfrist am Tag mit derselben Zahl, fehlt dieser Tag, am Monatsletzten.
Fällt das Ende auf Samstag, Sonntag oder einen übergebenen Feiertag, endet die
Frist am nächsten Werktag (§ 26 Abs. 3 SGB X).

Das Modul zeigt Fristen und eskaliert. Es entscheidet nicht, ob eine
Genehmigungsfiktion eingetreten ist. Das ist eine Rechtsfrage im Einzelfall.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

# § 18 Abs. 7 SGB IX: Abs. 1 bis 5 gelten nicht für diese Träger.
OHNE_P18 = frozenset(
    {"eingliederungshilfe", "jugendhilfe", "soziale_entschaedigung", "soldatenentschaedigung"}
)


class Ampel(StrEnum):
    GRUEN = "gruen"
    GELB = "gelb"
    ROT = "rot"
    ERLEDIGT = "erledigt"


@dataclass(frozen=True, slots=True)
class Frist:
    norm: str
    was: str
    ende: date
    ampel: Ampel
    hinweis: str = ""


def plus_monate(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def werktag(d: date, feiertage: Iterable[date] = ()) -> date:
    frei = set(feiertage)
    while d.weekday() >= 5 or d in frei:
        d += timedelta(days=1)
    return d


def _ampel(ende: date, heute: date, erledigt: date | None, vorwarnung: int) -> Ampel:
    if erledigt is not None and erledigt <= ende:
        return Ampel.ERLEDIGT
    if heute > ende or (erledigt is not None and erledigt > ende):
        return Ampel.ROT
    if (ende - heute).days <= vorwarnung:
        return Ampel.GELB
    return Ampel.GRUEN


@dataclass(frozen=True, slots=True)
class Antrag:
    eingang: date  # Eingang beim leistenden Träger (bei Weiterleitung: beim zweiten Träger)
    traeger: str = "rentenversicherung"
    zustaendigkeit_geklaert: date | None = None
    weitergeleitet: date | None = None
    gutachten_beauftragt: bool = False
    gutachten_liegt_vor: date | None = None
    begruendete_mitteilung: date | None = None
    angekuendigte_entscheidung: date | None = None
    entschieden: date | None = None


def berechne(
    a: Antrag,
    heute: date,
    feiertage: Iterable[date] = (),
    vorwarnung_tage: int = 3,
) -> list[Frist]:
    ft = tuple(feiertage)
    out: list[Frist] = []

    # § 14 Abs. 1: Zuständigkeit binnen zwei Wochen klären.
    ende = werktag(a.eingang + timedelta(weeks=2), ft)
    geklaert = a.zustaendigkeit_geklaert or a.weitergeleitet or a.entschieden
    out.append(
        Frist(
            "§ 14 Abs. 1 SGB IX",
            "Zuständigkeit klären oder weiterleiten",
            ende,
            _ampel(ende, heute, geklaert, vorwarnung_tage),
        )
    )

    if a.weitergeleitet is not None:
        out.append(
            Frist(
                "§ 14 Abs. 1 SGB IX",
                "Antrag weitergeleitet, Fristen laufen beim zweiten Träger ab dessen Eingang neu",
                a.weitergeleitet,
                Ampel.ERLEDIGT,
            )
        )
        return out

    # § 14 Abs. 2: Entscheidung.
    if a.gutachten_beauftragt:
        if a.gutachten_liegt_vor is not None:
            ende = werktag(a.gutachten_liegt_vor + timedelta(weeks=2), ft)
            out.append(
                Frist(
                    "§ 14 Abs. 2 S. 3 SGB IX",
                    "Entscheidung nach Gutachten",
                    ende,
                    _ampel(ende, heute, a.entschieden, vorwarnung_tage),
                )
            )
    else:
        ende = werktag(a.eingang + timedelta(weeks=3), ft)
        out.append(
            Frist(
                "§ 14 Abs. 2 S. 2 SGB IX",
                "Entscheidung ohne Gutachten",
                ende,
                _ampel(ende, heute, a.entschieden, vorwarnung_tage),
            )
        )

    # § 18: begründete Mitteilung vor Ablauf von zwei Monaten.
    if a.traeger in OHNE_P18:
        return out
    ende = werktag(plus_monate(a.eingang, 2), ft)
    fristgerecht_entschieden = a.entschieden is not None and a.entschieden <= ende
    erledigt = a.entschieden if fristgerecht_entschieden else a.begruendete_mitteilung
    ampel = _ampel(ende, heute, erledigt, vorwarnung_tage)
    hinweis = ""
    if ampel is Ampel.ROT and a.entschieden is None:
        hinweis = (
            "Keine fristgerechte begründete Mitteilung erfasst. Genehmigungsfiktion "
            "nach § 18 Abs. 3 SGB IX rechtlich prüfen."
        )
    out.append(
        Frist("§ 18 Abs. 1 SGB IX", "Entscheidung oder begründete Mitteilung", ende, ampel, hinweis)
    )

    if a.begruendete_mitteilung and a.angekuendigte_entscheidung and a.entschieden is None:
        ende = a.angekuendigte_entscheidung
        ampel = _ampel(ende, heute, None, vorwarnung_tage)
        out.append(
            Frist(
                "§ 18 Abs. 2, 3 SGB IX",
                "angekündigter Entscheidungstag",
                ende,
                ampel,
                "Ohne weitere begründete Mitteilung Fiktion prüfen." if ampel is Ampel.ROT else "",
            )
        )
    return out
