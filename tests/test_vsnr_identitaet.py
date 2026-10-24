from __future__ import annotations

from datetime import date

import pytest

from akten_interlock import vsnr
from akten_interlock.identitaet import Ergebnis, normalisiere_name, pruefe
from akten_interlock.modelle import Akte, Merkmale


def test_pruefziffer_bekanntes_beispiel() -> None:
    # Häufig zitiertes Lehrbuchbeispiel: 65 170839 J 00 3
    assert vsnr.pruefziffer("65170839J00") == 3
    assert vsnr.parse("65 170839 J 003").text == "65170839J003"


def test_falsche_pruefziffer() -> None:
    with pytest.raises(vsnr.VsnrFehler, match="Prüfziffer"):
        vsnr.parse("65170839J004")


@pytest.mark.parametrize("raw", ["", "123", "65170839J00X", "65999939J003", "65171339J003"])
def test_format(raw: str) -> None:
    with pytest.raises(vsnr.VsnrFehler):
        vsnr.parse(raw)


def test_erzeugte_nummer_ist_konsistent() -> None:
    geb = date(1990, 12, 1)
    v = vsnr.parse(vsnr.erzeuge("40", geb, "Özdemir"))
    assert v.passt_zu_geburtsdatum(geb)
    assert v.passt_zu_geburtsname("Özdemir")
    assert v.buchstabe == "O"


def test_namen_normalisieren() -> None:
    assert normalisiere_name(" Müller-Lüdenscheidt ") == "muellerluedenscheidt"
    assert normalisiere_name("Mueller") == normalisiere_name("Müller")
    assert normalisiere_name("José") == "jose"


def test_match(akte: Akte) -> None:
    assert pruefe(akte, akte.merkmale).ergebnis is Ergebnis.MATCH


def test_nur_vsnr_reicht_nicht(akte: Akte) -> None:
    assert pruefe(akte, Merkmale(vsnr=akte.vsnr)).ergebnis is Ergebnis.UNKLAR


def test_ohne_vsnr_unklar(akte: Akte) -> None:
    m = Merkmale(None, "Muster", "Erika", akte.geburtsdatum)
    assert pruefe(akte, m).ergebnis is Ergebnis.UNKLAR


def test_ein_widerspruch_genuegt(akte: Akte) -> None:
    m = Merkmale(akte.vsnr, "Muster", "Erika", date(1985, 3, 16))
    p = pruefe(akte, m)
    assert p.ergebnis is Ergebnis.ABWEICHEND
    assert "Geburtsdatum weicht ab" in p.gruende


def test_fremde_person(akte: Akte, fremd: Merkmale) -> None:
    assert pruefe(akte, fremd).ergebnis is Ergebnis.ABWEICHEND


def test_ungueltige_vsnr_auf_dokument(akte: Akte) -> None:
    m = Merkmale(
        akte.vsnr[:-1] + str((int(akte.vsnr[-1]) + 1) % 10), "Muster", "Erika", akte.geburtsdatum
    )
    assert pruefe(akte, m).ergebnis is Ergebnis.UNKLAR


def test_geburtsname_zaehlt(akte: Akte) -> None:
    akte.geburtsname = "Altname"
    m = Merkmale(akte.vsnr, "Altname", None, akte.geburtsdatum)
    assert pruefe(akte, m).ergebnis is Ergebnis.MATCH
