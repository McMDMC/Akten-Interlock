"""Smartphone-Foto-Aufbereitung: aus einem schrägen, farbstichigen Handyfoto ein gerades Blatt.

Schritte:
1. Laden, auch HEIC/HEIF (pillow-heif), EXIF-Drehung anwenden.
2. Blatt im Foto finden (Kanten bzw. helle Fläche, größtes Viereck) und perspektivisch
   entzerren (OpenCV).
3. Schatten und Farbstich entfernen: Hintergrund schätzen und herausrechnen.
   Standard Graustufen; Modus "farbe" behält Farben und gleicht nur Weißabgleich und
   Schatten an.
4. Qualität messen: effektive Auflösung bezogen auf DIN A4 und Schärfe. Mängel werden
   gemeldet (Rückfrage an den Absender), die Datei geht trotzdem nicht verloren.

Das Original bleibt immer unverändert erhalten. Diese Aufbereitung ist eine zusätzliche
Lesefassung, kein Ersatz.

Abhängigkeiten (Extra ``foto``): opencv-python-headless, numpy, pillow, pillow-heif.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps

try:  # HEIC/HEIF ist optional
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC = True
except ImportError:  # pragma: no cover - abhängig von der Installation
    HEIC = False

Bild = NDArray[np.uint8]

A4_KURZ_MM = 210.0
MIN_DPI = 150  # darunter wird es für Texterkennung und Lesbarkeit knapp
EMPF_DPI = 300
MIN_SCHAERFE = 80.0  # Varianz des Laplace-Filters auf 1000 px Breite, empirisch
MIN_FLAECHE = 0.20  # Blatt muss mindestens 20 % des Fotos einnehmen


@dataclass(frozen=True, slots=True)
class Qualitaet:
    breite: int
    hoehe: int
    dpi: int
    schaerfe: float
    maengel: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.maengel


@dataclass(frozen=True, slots=True)
class FotoErgebnis:
    ziel: Path  # aufbereitete Fassung (PNG, verlustfrei, mit DPI-Angabe)
    blatt_gefunden: bool
    ecken: tuple[tuple[int, int], ...]
    qualitaet: Qualitaet
    schritte: tuple[str, ...]


# ----------------------------------------------------------------- laden


def lade(pfad: Path) -> Bild:
    """Lädt JPEG/PNG/TIFF/HEIC als RGB-Array, EXIF-Drehung bereits angewendet."""
    with Image.open(pfad) as roh:
        rgb = ImageOps.exif_transpose(roh).convert("RGB")
        return np.asarray(rgb, dtype=np.uint8).copy()


# ----------------------------------------------------------------- Blatt finden


def _ordne(pts: NDArray[Any]) -> NDArray[np.float32]:
    """Ecken in Reihenfolge oben-links, oben-rechts, unten-rechts, unten-links."""
    p = pts.reshape(4, 2).astype(np.float32)
    s, d = p.sum(axis=1), np.diff(p, axis=1).ravel()
    return np.array(
        [p[np.argmin(s)], p[np.argmin(d)], p[np.argmax(s)], p[np.argmax(d)]], dtype=np.float32
    )


def _viereck(kontur: NDArray[Any]) -> NDArray[Any] | None:
    huelle = cv2.convexHull(kontur)
    umfang = cv2.arcLength(huelle, True)
    for eps in (0.02, 0.03, 0.04, 0.05):
        approx = cv2.approxPolyDP(huelle, eps * umfang, True)
        if len(approx) == 4:
            return approx
    return None


def finde_blatt(rgb: Bild) -> NDArray[np.float32] | None:
    """Sucht das größte Viereck (das Blatt). Gibt 4 Ecken in Originalkoordinaten zurück."""
    h, w = rgb.shape[:2]
    f = 1000.0 / max(h, w)
    klein = cv2.resize(rgb, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA)
    grau = cv2.GaussianBlur(cv2.cvtColor(klein, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    kern = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

    # Zwei Wege: Kanten (Blatt vor beliebigem Hintergrund) und helle Fläche (Otsu).
    kanten = cv2.dilate(cv2.Canny(grau, 50, 150), kern, iterations=2)
    _, hell = cv2.threshold(grau, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    hell = cv2.morphologyEx(hell, cv2.MORPH_CLOSE, kern, iterations=3)

    flaeche = klein.shape[0] * klein.shape[1]
    bestes: NDArray[Any] | None = None
    beste_flaeche = MIN_FLAECHE * flaeche
    for maske in (kanten, hell):
        konturen, _ = cv2.findContours(maske, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for k in sorted(konturen, key=cv2.contourArea, reverse=True)[:5]:
            q = _viereck(k)
            if q is None:
                continue
            a = cv2.contourArea(q)
            if a > beste_flaeche and a < 0.98 * flaeche:  # nicht nur der Bildrand
                bestes, beste_flaeche = q, a
    if bestes is None:
        return None
    return _ordne(bestes / f)


def entzerre(rgb: Bild, ecken: NDArray[np.float32]) -> Bild:
    tl, tr, br, bl = ecken
    breite = int(round(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))))
    hoehe = int(round(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))))
    ziel = np.array(
        [[0, 0], [breite - 1, 0], [breite - 1, hoehe - 1], [0, hoehe - 1]], dtype=np.float32
    )
    m = cv2.getPerspectiveTransform(ecken, ziel)
    out = cv2.warpPerspective(
        rgb, m, (breite, hoehe), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    # Schmalen Rand abschneiden: Reste von Tischkante und Blattschatten.
    r = max(2, round(0.006 * min(breite, hoehe)))
    return np.ascontiguousarray(out[r:-r, r:-r], dtype=np.uint8)


# ----------------------------------------------------------------- Farbe, Schatten


def _ohne_hintergrund(kanal: Bild) -> Bild:
    """Schätzt Papierhelligkeit inkl. Schatten und rechnet sie heraus: Papier -> weiß."""
    k = max(15, (min(kanal.shape[:2]) // 40) | 1)
    hintergrund = cv2.medianBlur(cv2.dilate(kanal, np.ones((7, 7), np.uint8)), k)
    norm = cv2.divide(kanal, hintergrund, scale=255)
    return np.asarray(norm, dtype=np.uint8)


def normalisiere_farbe(rgb: Bild, modus: str = "graustufen") -> Bild:
    if modus == "graustufen":
        grau = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        return _ohne_hintergrund(np.asarray(grau, dtype=np.uint8))
    if modus == "farbe":
        kanaele = [_ohne_hintergrund(np.ascontiguousarray(rgb[:, :, i])) for i in range(3)]
        return np.dstack(kanaele).astype(np.uint8)
    raise ValueError(f"modus {modus!r} unbekannt (graustufen|farbe)")


# ----------------------------------------------------------------- Qualität


def schaerfe(bild: Bild) -> float:
    grau = bild if bild.ndim == 2 else cv2.cvtColor(bild, cv2.COLOR_RGB2GRAY)
    f = 1000.0 / grau.shape[1]
    g = cv2.resize(grau, (1000, max(1, round(grau.shape[0] * f))), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def bewerte(bild: Bild, blatt_gefunden: bool) -> Qualitaet:
    h, w = bild.shape[:2]
    dpi = int(min(h, w) / (A4_KURZ_MM / 25.4))
    s = schaerfe(bild)
    maengel: list[str] = []
    if dpi < MIN_DPI:
        maengel.append(f"Auflösung ca. {dpi} dpi, unter {MIN_DPI} dpi")
    if s < MIN_SCHAERFE:
        maengel.append(f"unscharf (Schärfewert {s:.0f} < {MIN_SCHAERFE:.0f})")
    if not blatt_gefunden:
        maengel.append("Blattränder nicht erkannt, nur begradigt")
    return Qualitaet(w, h, dpi, round(s, 1), tuple(maengel))


# ----------------------------------------------------------------- Ablauf


def bereite_auf(src: Path, zielordner: Path, modus: str = "graustufen") -> FotoErgebnis:
    zielordner.mkdir(parents=True, exist_ok=True)
    schritte = [f"geladen ({src.suffix.lower().lstrip('.') or 'ohne Endung'})"]
    rgb = lade(src)

    ecken = finde_blatt(rgb)
    if ecken is not None:
        rgb = entzerre(rgb, ecken)
        schritte.append("Blatt erkannt, Perspektive entzerrt")
    else:
        schritte.append("kein Blatt erkannt, Bild unverändert übernommen")

    bild = normalisiere_farbe(rgb, modus)
    schritte.append(f"Schatten und Farbstich entfernt ({modus})")

    q = bewerte(bild, ecken is not None)
    dpi = max(q.dpi, 72)
    ziel = zielordner / f"{src.stem}.aufbereitet.png"
    Image.fromarray(bild).save(ziel, dpi=(dpi, dpi))
    schritte.append(f"Lesefassung {q.breite}x{q.hoehe} px, ca. {q.dpi} dpi")
    if not q.ok:
        schritte.append("Rückfrage an Absender empfohlen: " + "; ".join(q.maengel))

    eck = tuple((int(x), int(y)) for x, y in ecken) if ecken is not None else ()
    return FotoErgebnis(ziel, ecken is not None, eck, q, tuple(schritte))
