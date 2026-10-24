"""Identitätsprüfung vor dem Ablegen: passt das Dokument zur Akte?

Regeln (fail-closed):
* Ein widersprechendes Merkmal genügt für ABWEICHEND. Dann nie in die Akte.
* MATCH nur, wenn die VSNR gültig und gleich ist UND mindestens ein weiteres
  Merkmal (Nachname oder Geburtsdatum) übereinstimmt.
* Alles andere ist UNKLAR und braucht eine namentliche Prüfung durch einen Menschen.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from . import vsnr as vsnr_mod
from .modelle import Akte, Merkmale

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def normalisiere_name(name: str) -> str:
    n = name.strip().casefold().translate(_UMLAUTE)
    n = unicodedata.normalize("NFKD", n)
    n = "".join(c for c in n if not unicodedata.combining(c))
    return "".join(c for c in n if c.isalnum())


class Ergebnis(StrEnum):
    MATCH = "match"
    UNKLAR = "unklar"
    ABWEICHEND = "abweichend"


@dataclass(frozen=True, slots=True)
class Pruefung:
    ergebnis: Ergebnis
    gruende: tuple[str, ...]

    @property
    def text(self) -> str:
        return "; ".join(self.gruende)


def pruefe(akte: Akte, dok: Merkmale) -> Pruefung:
    widerspruch: list[str] = []
    treffer: list[str] = []
    offen: list[str] = []

    vsnr_ok = False
    if dok.vsnr:
        try:
            v = vsnr_mod.parse(dok.vsnr)
        except vsnr_mod.VsnrFehler as e:
            offen.append(f"VSNR auf Dokument ungültig ({e})")
        else:
            if v.text == vsnr_mod.normalisiere(akte.vsnr):
                vsnr_ok = True
                treffer.append("VSNR gleich")
            else:
                widerspruch.append("VSNR weicht ab")
    else:
        offen.append("keine VSNR auf dem Dokument")

    if dok.nachname:
        if normalisiere_name(dok.nachname) in {
            normalisiere_name(akte.nachname),
            normalisiere_name(akte.geburtsname or akte.nachname),
        }:
            treffer.append("Nachname gleich")
        else:
            widerspruch.append("Nachname weicht ab")
    if dok.vorname and normalisiere_name(dok.vorname) != normalisiere_name(akte.vorname):
        widerspruch.append("Vorname weicht ab")
    if dok.geburtsdatum:
        if dok.geburtsdatum == akte.geburtsdatum:
            treffer.append("Geburtsdatum gleich")
        else:
            widerspruch.append("Geburtsdatum weicht ab")

    if widerspruch:
        return Pruefung(Ergebnis.ABWEICHEND, tuple(widerspruch))
    if vsnr_ok and len(treffer) >= 2:
        return Pruefung(Ergebnis.MATCH, tuple(treffer))
    return Pruefung(Ergebnis.UNKLAR, tuple(offen + treffer) or ("zu wenig Merkmale",))
