"""Identitätsmerkmale aus OCR-Text vorschlagen. KI optional, austauschbar, nie entscheidend.

* RegelExtraktor: deterministisch, ohne KI, Standard.
* ModelRouter: bindet beliebige Modelle über eine schmale Schnittstelle an (lokal oder
  Cloud). Fällt ein Modell aus oder liefert Unsinn, geht es zum nächsten, zuletzt zur
  Regel-Extraktion. Der Router hängt an keinem Anbieter.

Egal woher die Merkmale kommen: Abgelegt wird nur, was die Identitätsprüfung und die
Policy durchlassen. Die KI schlägt vor, der Prüfkern entscheidet.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from .modelle import Merkmale

_VSNR = re.compile(r"\b(\d{2}\s?\d{6}\s?[A-Z]\s?\d{2}\s?\d)\b")
_GEB = re.compile(r"geb(?:oren|\.)?\s*(?:am)?\s*:?\s*(\d{1,2}\.\d{1,2}\.\d{4})", re.I)
_NAME = re.compile(r"Name\s*:\s*([^\n,]+?)\s*,\s*([^\n]+)")


class Extraktor(Protocol):
    name: str

    def extrahiere(self, text: str) -> Merkmale: ...


class RegelExtraktor:
    name = "regeln"

    def extrahiere(self, text: str) -> Merkmale:
        vsnr = m.group(1).replace(" ", "") if (m := _VSNR.search(text)) else None
        geb = None
        if g := _GEB.search(text):
            try:
                geb = datetime.strptime(g.group(1), "%d.%m.%Y").date()
            except ValueError:
                geb = None
        nach = vor = None
        if n := _NAME.search(text):
            nach, vor = n.group(1).strip(), n.group(2).strip()
        return Merkmale(vsnr, nach, vor, geb)


@dataclass(frozen=True, slots=True)
class ModellAdapter:
    """Ein Modell hinter einer Funktion ``text -> dict``. Woher, ist egal."""

    name: str
    aufruf: Callable[[str], dict[str, str | None]]

    def extrahiere(self, text: str) -> Merkmale:
        raw = self.aufruf(text)
        geb_raw = raw.get("geburtsdatum")
        geb = date.fromisoformat(geb_raw) if geb_raw else None
        return Merkmale(raw.get("vsnr"), raw.get("nachname"), raw.get("vorname"), geb)


class ModelRouter:
    def __init__(self, kette: Sequence[Extraktor], rueckfall: Extraktor | None = None) -> None:
        self.kette = list(kette)
        self.rueckfall = rueckfall or RegelExtraktor()
        self.letzter: str = ""

    def extrahiere(self, text: str) -> Merkmale:
        for ex in self.kette:
            try:
                m = ex.extrahiere(text)
            except Exception:  # noqa: BLE001 - Modellfehler -> nächstes Modell
                continue
            if m.vsnr or m.geburtsdatum:
                self.letzter = ex.name
                return m
        self.letzter = self.rueckfall.name
        return self.rueckfall.extrahiere(text)
