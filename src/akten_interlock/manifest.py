"""Export-Manifest: Inhaltsverzeichnis mit Blattzahlen, Hashes und Darstellungsform.

Löst zwei Probleme aus dem Ausgangsfall:
* "Aus technischen Gründen nicht in der PDF": Jedes Dokument erscheint im Manifest.
  Was nicht nach PDF/A konvertiert werden konnte, wird im Original beigefügt und so markiert.
* "Auf welcher Seite steht das?": Jedes Dokument hat einen festen Blattbereich.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .modelle import Akte


@dataclass(frozen=True, slots=True)
class Eintrag:
    lfd: int
    dok_id: str
    titel: str
    eingang: str
    blatt_von: int
    blatt_bis: int
    seiten: int
    sha256: str
    format: str
    darstellung: str


@dataclass(frozen=True, slots=True)
class Manifest:
    akte_id: str
    eintraege: tuple[Eintrag, ...]
    seiten_gesamt: int
    vollstaendig: bool
    maengel: tuple[str, ...]

    @property
    def sha256(self) -> str:
        body = json.dumps([asdict(e) for e in self.eintraege], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(f"{self.akte_id}|{body}".encode()).hexdigest()

    def als_text(self) -> str:
        zeilen = [f"Inhaltsverzeichnis Akte {self.akte_id} ({self.seiten_gesamt} Blatt)"]
        for e in self.eintraege:
            mark = "" if e.darstellung == "pdfa" else f"  [{e.format.upper()} im Original]"
            bl = f"{e.blatt_von}" if e.blatt_von == e.blatt_bis else f"{e.blatt_von}-{e.blatt_bis}"
            zeilen.append(f"{e.lfd:>3}. Bl. {bl:<7} {e.eingang}  {e.titel}{mark}")
        zeilen.append(f"Manifest-SHA-256: {self.sha256}")
        return "\n".join(zeilen)


def erstelle(akte: Akte) -> Manifest:
    eintraege: list[Eintrag] = []
    maengel: list[str] = []
    blatt = 1
    gesehen: set[str] = set()
    for i, d in enumerate(sorted(akte.dokumente, key=lambda d: (d.eingang, d.dok_id)), 1):
        if d.dok_id in gesehen:
            maengel.append(f"{d.dok_id}: doppelt")
        gesehen.add(d.dok_id)
        if d.seiten < 1:
            maengel.append(f"{d.dok_id}: keine Seiten erfasst")
        if len(d.sha256) != 64:
            maengel.append(f"{d.dok_id}: kein gültiger Hash, Datei fehlt?")
        if d.darstellung not in ("pdfa", "original_beigefuegt"):
            maengel.append(f"{d.dok_id}: Darstellung {d.darstellung!r} unbekannt")
        seiten = max(d.seiten, 0)
        eintraege.append(
            Eintrag(
                i,
                d.dok_id,
                d.titel,
                d.eingang.isoformat(),
                blatt,
                blatt + seiten - 1,
                seiten,
                d.sha256,
                d.format,
                d.darstellung,
            )
        )
        blatt += seiten
    return Manifest(akte.akte_id, tuple(eintraege), blatt - 1, not maengel, tuple(maengel))
