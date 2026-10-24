"""Versicherungsnummer der Rentenversicherung (VSNR): Format- und Prüfziffernprüfung.

Aufbau (12 Zeichen): BB TTMMJJ A SS P
  BB      Bereichsnummer
  TTMMJJ  Geburtsdatum
  A       Anfangsbuchstabe des Geburtsnamens
  SS      Seriennummer
  P       Prüfziffer

Prüfziffer: Buchstabe durch seine zweistellige Position im Alphabet ersetzen (A=01 ... Z=26),
die so entstehenden 12 Ziffern mit 2,1,2,5,7,1,2,1,2,1,2,1 gewichten, von jedem Produkt die
Quersumme bilden, alles addieren; Prüfziffer = Summe modulo 10.

Nur synthetische Nummern in Tests und Demo verwenden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_WEIGHTS = (2, 1, 2, 5, 7, 1, 2, 1, 2, 1, 2, 1)
_FORMAT = re.compile(r"(\d{2})(\d{2})(\d{2})(\d{2})([A-Z])(\d{2})(\d)")


def normalisiere(raw: str) -> str:
    return re.sub(r"\s+", "", raw).upper()


def pruefziffer(ohne_pz: str) -> int:
    """Prüfziffer für die ersten 11 Zeichen einer VSNR."""
    s = normalisiere(ohne_pz)
    if len(s) != 11 or not s[:8].isdigit() or not s[8].isalpha() or not s[9:].isdigit():
        raise ValueError("erwartet 11 Zeichen: 8 Ziffern, 1 Buchstabe, 2 Ziffern")
    digits = s[:8] + f"{ord(s[8]) - ord('A') + 1:02d}" + s[9:]
    total = 0
    for d, w in zip(digits, _WEIGHTS, strict=True):
        p = int(d) * w
        total += p // 10 + p % 10
    return total % 10


@dataclass(frozen=True, slots=True)
class Vsnr:
    bereich: str
    geburtsdatum_ttmmjj: str
    buchstabe: str
    serie: str
    pz: int

    @property
    def text(self) -> str:
        return f"{self.bereich}{self.geburtsdatum_ttmmjj}{self.buchstabe}{self.serie}{self.pz}"

    def passt_zu_geburtsdatum(self, geb: date) -> bool:
        return self.geburtsdatum_ttmmjj == geb.strftime("%d%m%y")

    def passt_zu_geburtsname(self, geburtsname: str) -> bool:
        from .identitaet import normalisiere_name

        n = normalisiere_name(geburtsname)
        return bool(n) and n[0].upper() == self.buchstabe


class VsnrFehler(ValueError):
    pass


def parse(raw: str) -> Vsnr:
    """Formatprüfung und Prüfziffer. Wirft VsnrFehler bei ungültiger Nummer."""
    s = normalisiere(raw)
    m = _FORMAT.fullmatch(s)
    if not m:
        raise VsnrFehler("VSNR hat nicht das Format BB TTMMJJ A SS P")
    bereich, tt, mm, jj, buchstabe, serie, pz = m.groups()
    if not (1 <= int(tt) <= 31 and 1 <= int(mm) <= 12):
        raise VsnrFehler("Geburtsdatum in der VSNR ist kein gültiges Datum")
    if pruefziffer(s[:11]) != int(pz):
        raise VsnrFehler("Prüfziffer stimmt nicht")
    return Vsnr(bereich, tt + mm + jj, buchstabe, serie, int(pz))


def erzeuge(bereich: str, geb: date, geburtsname: str, serie: str = "00") -> str:
    """Synthetische, formal gültige VSNR für Tests und Demo."""
    from .identitaet import normalisiere_name

    stem = f"{bereich}{geb.strftime('%d%m%y')}{normalisiere_name(geburtsname)[0].upper()}{serie}"
    return stem + str(pruefziffer(stem))
