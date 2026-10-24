"""Datenmodell. Der Prüfkern sieht nur Metadaten, nie den Inhalt eines Dokuments."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True, slots=True)
class Merkmale:
    """Identitätsmerkmale, wie sie auf einem Dokument stehen (oder fehlen)."""

    vsnr: str | None = None
    nachname: str | None = None
    vorname: str | None = None
    geburtsdatum: date | None = None


@dataclass(slots=True)
class Akte:
    akte_id: str
    vsnr: str
    nachname: str
    vorname: str
    geburtsdatum: date
    geburtsname: str | None = None
    dokumente: list[Dokument] = field(default_factory=list)

    @property
    def merkmale(self) -> Merkmale:
        return Merkmale(self.vsnr, self.nachname, self.vorname, self.geburtsdatum)


@dataclass(slots=True)
class Dokument:
    dok_id: str
    titel: str
    quelle: str  # z. B. "fax", "post", "egvp", "intern"
    eingang: date
    seiten: int
    sha256: str
    format: str  # "pdfa", "pdf", "tiff", "jpeg", ...
    merkmale: Merkmale
    darstellung: str = "pdfa"  # "pdfa" oder "original_beigefuegt"
    hinweis: str = ""
