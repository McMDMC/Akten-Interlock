from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
from PIL import Image  # noqa: E402

from akten_interlock import foto, normalisierung  # noqa: E402

from .testbild import handyfoto  # noqa: E402

A4 = 297 / 210


@pytest.fixture(scope="module")
def ordner(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("foto")


@pytest.fixture(scope="module")
def schraeg(ordner: Path) -> tuple[Path, np.ndarray, foto.FotoErgebnis]:
    p = ordner / "schraeg.jpg"
    ecken = handyfoto(p)
    return p, ecken, foto.bereite_auf(p, ordner / "out")


def test_blatt_wird_gefunden(schraeg: tuple[Path, np.ndarray, foto.FotoErgebnis]) -> None:
    _, ecken, r = schraeg
    assert r.blatt_gefunden
    # Abweichung der Ecken unter 1 % der Bildbreite (3000 px)
    assert np.abs(np.array(r.ecken) - ecken).max() < 30


def test_entzerrt_ungefaehr_a4(schraeg: tuple[Path, np.ndarray, foto.FotoErgebnis]) -> None:
    q = schraeg[2].qualitaet
    assert abs(q.hoehe / q.breite - A4) / A4 < 0.06
    assert q.ok and q.dpi >= foto.EMPF_DPI * 0.8


def test_schatten_und_farbstich_weg(schraeg: tuple[Path, np.ndarray, foto.FotoErgebnis]) -> None:
    g = np.asarray(Image.open(schraeg[2].ziel))
    assert g.ndim == 2  # Graustufen
    links, rechts = g[60:160, 30:130].mean(), g[60:160, -130:-30].mean()
    unten = g[-160:-60, 30:130].mean()
    assert min(links, rechts, unten) > 235  # Papier ist weiß
    assert abs(links - rechts) < 8  # Schatten von links ist ausgeglichen


def test_farbmodus_neutral(schraeg: tuple[Path, np.ndarray, foto.FotoErgebnis]) -> None:
    rgb = foto.entzerre(foto.lade(schraeg[0]), foto.finde_blatt(foto.lade(schraeg[0])))
    bunt = foto.normalisiere_farbe(rgb, "farbe")
    rand = bunt[60:160, -130:-30].reshape(-1, 3).mean(axis=0)
    assert bunt.ndim == 3 and rand.max() - rand.min() < 10  # kein Gelbstich mehr


def test_unscharf_wird_gemeldet(ordner: Path) -> None:
    p = ordner / "unscharf.jpg"
    handyfoto(p, unscharf=True)
    q = foto.bereite_auf(p, ordner / "out").qualitaet
    assert not q.ok and any("unscharf" in m for m in q.maengel)


def test_zu_klein_wird_gemeldet(ordner: Path) -> None:
    p = ordner / "klein.jpg"
    handyfoto(p, klein=True)
    q = foto.bereite_auf(p, ordner / "out").qualitaet
    assert any("dpi" in m for m in q.maengel)


def test_ohne_blatt(ordner: Path) -> None:
    p = ordner / "rauschen.png"
    rng = np.random.default_rng(1)
    Image.fromarray(rng.integers(0, 255, (800, 600, 3), dtype=np.uint8)).save(p)
    r = foto.bereite_auf(p, ordner / "out")
    assert not r.blatt_gefunden
    assert any("Blattränder" in m for m in r.qualitaet.maengel)


def test_heic(ordner: Path) -> None:
    pytest.importorskip("pillow_heif")
    jpg = ordner / "fuer_heic.jpg"
    handyfoto(jpg)
    heic = ordner / "handy.heic"
    Image.open(jpg).save(heic, quality=90)
    r = foto.bereite_auf(heic, ordner / "out")
    assert r.blatt_gefunden and r.qualitaet.ok


def test_exif_drehung(ordner: Path) -> None:
    p = ordner / "gedreht.jpg"
    im = Image.new("RGB", (400, 300), "white")
    exif = im.getexif()
    exif[0x0112] = 6  # Orientation: 90° im Uhrzeigersinn anzeigen
    im.save(p, exif=exif)
    assert foto.lade(p).shape[:2] == (400, 300)


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract fehlt")
def test_ocr_liest_aufbereitung(schraeg: tuple[Path, np.ndarray, foto.FotoErgebnis]) -> None:
    langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
    lang = "deu" if "deu" in langs.split() else "eng"
    text = subprocess.run(
        ["tesseract", str(schraeg[2].ziel), "-", "-l", lang], capture_output=True, text=True
    ).stdout
    assert "Muster" in text and "15.03.1985" in text


@pytest.mark.skipif(
    not (shutil.which("ocrmypdf") and shutil.which("gs")), reason="ocrmypdf mit Ghostscript fehlt"
)
def test_handyfoto_wird_pdfa(ordner: Path) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    pypdf = pytest.importorskip("pypdf")
    p = ordner / "antrag.jpg"
    handyfoto(p)
    langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
    r = normalisierung.normalisiere(p, ordner / "pdf", sprache="deu" if "deu" in langs else "eng")
    assert r.darstellung == "pdfa" and r.maengel == ()
    assert r.original.read_bytes() == p.read_bytes()  # Original unverändert daneben
    with pikepdf.open(r.ziel) as pdf:
        assert pdf.open_metadata().pdfa_status.startswith("2")
    text = pypdf.PdfReader(r.ziel).pages[0].extract_text()
    assert "Muster" in text
