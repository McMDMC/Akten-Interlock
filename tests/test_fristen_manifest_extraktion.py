from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from akten_interlock import extraktion, normalisierung
from akten_interlock.fristen import Ampel, Antrag, berechne, plus_monate, werktag
from akten_interlock.manifest import erstelle
from akten_interlock.modelle import Akte, Merkmale

from .conftest import dok


def test_monatsende() -> None:
    assert plus_monate(date(2025, 12, 31), 2) == date(2026, 2, 28)
    assert plus_monate(date(2024, 12, 31), 2) == date(2025, 2, 28)
    assert plus_monate(date(2023, 12, 31), 2) == date(2024, 2, 29)
    assert plus_monate(date(2025, 11, 15), 2) == date(2026, 1, 15)


def test_wochenende_und_feiertag() -> None:
    assert werktag(date(2025, 5, 3)) == date(2025, 5, 5)  # Sa -> Mo
    assert werktag(date(2025, 12, 25), [date(2025, 12, 25), date(2025, 12, 26)]) == date(
        2025, 12, 29
    )


def by_norm(fs: list, prefix: str):  # type: ignore[no-untyped-def,type-arg]
    return next(f for f in fs if f.norm.startswith(prefix))


def test_fristen_ohne_gutachten() -> None:
    fs = berechne(Antrag(eingang=date(2025, 8, 25)), heute=date(2025, 9, 1))
    assert by_norm(fs, "§ 14 Abs. 1").ende == date(2025, 9, 8)
    assert by_norm(fs, "§ 14 Abs. 2").ende == date(2025, 9, 15)
    assert by_norm(fs, "§ 18 Abs. 1").ende == date(2025, 10, 27)  # 25.10. ist Samstag
    assert by_norm(fs, "§ 18 Abs. 1").ampel is Ampel.GRUEN


def test_p18_rot_mit_hinweis() -> None:
    f = by_norm(berechne(Antrag(eingang=date(2025, 8, 25)), date(2026, 10, 22)), "§ 18 Abs. 1")
    assert f.ampel is Ampel.ROT and "Genehmigungsfiktion" in f.hinweis


def test_p18_mitteilung_rechtzeitig() -> None:
    a = Antrag(
        eingang=date(2025, 8, 25),
        begruendete_mitteilung=date(2025, 10, 1),
        angekuendigte_entscheidung=date(2025, 11, 30),
    )
    fs = berechne(a, date(2025, 12, 5))
    assert by_norm(fs, "§ 18 Abs. 1").ampel is Ampel.ERLEDIGT
    assert by_norm(fs, "§ 18 Abs. 2").ampel is Ampel.ROT


def test_p18_gilt_nicht_fuer_eingliederungshilfe() -> None:
    fs = berechne(
        Antrag(eingang=date(2025, 8, 25), traeger="eingliederungshilfe"), date(2026, 1, 1)
    )
    assert not any(f.norm.startswith("§ 18") for f in fs)


def test_mit_gutachten() -> None:
    a = Antrag(
        eingang=date(2025, 8, 25), gutachten_beauftragt=True, gutachten_liegt_vor=date(2025, 9, 10)
    )
    fs = berechne(a, date(2025, 9, 22))
    f = by_norm(fs, "§ 14 Abs. 2 S. 3")
    assert f.ende == date(2025, 9, 24) and f.ampel is Ampel.GELB


def test_weiterleitung_beendet_fristen_beim_ersten_traeger() -> None:
    fs = berechne(
        Antrag(eingang=date(2025, 8, 25), weitergeleitet=date(2025, 9, 1)), date(2026, 1, 1)
    )
    assert all(f.ampel is Ampel.ERLEDIGT for f in fs)


def test_manifest_blattzahlen(akte: Akte) -> None:
    akte.dokumente += [
        dok(akte.merkmale, seiten=3),
        dok(akte.merkmale, seiten=1, fmt="tiff", darstellung="original_beigefuegt"),
    ]
    m = erstelle(akte)
    assert m.vollstaendig and m.seiten_gesamt == 4
    assert (m.eintraege[1].blatt_von, m.eintraege[1].blatt_bis) == (4, 4)
    assert "TIFF im Original" in m.als_text()
    h = m.sha256
    akte.dokumente[0].seiten = 4
    assert erstelle(akte).sha256 != h


def test_extraktion_regeln() -> None:
    txt = "Name: Muster, Erika\ngeb. am 15.03.1985\nVSNR 12 150385 M 00 7"
    m = extraktion.RegelExtraktor().extrahiere(txt)
    assert m.vsnr == "12150385M007"
    assert (m.nachname, m.vorname, m.geburtsdatum) == ("Muster", "Erika", date(1985, 3, 15))


def test_router_faellt_auf_regeln_zurueck() -> None:
    def kaputt(_: str) -> dict[str, str | None]:
        raise TimeoutError

    def leer(_: str) -> dict[str, str | None]:
        return {}

    r = extraktion.ModelRouter(
        [extraktion.ModellAdapter("modell-a", kaputt), extraktion.ModellAdapter("modell-b", leer)]
    )
    m = r.extrahiere("geb. 01.02.1990")
    assert r.letzter == "regeln" and m.geburtsdatum == date(1990, 2, 1)


def test_router_nimmt_erstes_brauchbares_modell() -> None:
    r = extraktion.ModelRouter(
        [
            extraktion.ModellAdapter(
                "lokal", lambda _: {"vsnr": "65170839J003", "geburtsdatum": "1939-08-17"}
            )
        ]
    )
    m = r.extrahiere("egal")
    assert r.letzter == "lokal" and m.vsnr == "65170839J003"
    assert isinstance(m, Merkmale)


def test_normalisierung_behaelt_original(tmp_path: Path) -> None:
    src = tmp_path / "befund.xyz"
    src.write_bytes(b"nicht konvertierbar")
    r = normalisierung.normalisiere(src, tmp_path / "out")
    assert r.darstellung == "original_beigefuegt"
    assert r.ziel.read_bytes() == src.read_bytes()
    assert r.sha256_original == normalisierung.sha256_datei(src)


def test_normalisierung_bild(tmp_path: Path) -> None:
    pytest.importorskip("img2pdf")
    pytest.importorskip("PIL")
    from PIL import Image

    src = tmp_path / "scan.png"
    Image.new("RGB", (200, 100), "white").save(src)
    r = normalisierung.normalisiere(src, tmp_path / "out", sprache="eng")
    # Mit vollständigem ocrmypdf-Stack (inkl. Ghostscript) PDF/A, sonst Original.
    assert r.darstellung in ("pdfa", "original_beigefuegt")
    assert r.ziel.exists() and r.seiten == 1
