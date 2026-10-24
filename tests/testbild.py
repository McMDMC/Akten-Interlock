"""Erzeugt ein künstlich verzerrtes 'Handyfoto' eines Blatts: schräg, Farbstich, Schatten.

Nur synthetische Inhalte. Wird von Tests und Demo genutzt.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from akten_interlock import vsnr

FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
ZEILEN = [
    "Name: Muster, Erika",
    "geb. am 15.03.1985",
    "VSNR " + vsnr.erzeuge("12", date(1985, 3, 15), "Muster"),
    "",
    "Antrag auf Leistungen zur Teilhabe",
    "am Arbeitsleben",
    "",
    "Hiermit beantrage ich eine Umschulung",
    "zum Fachinformatiker Systemintegration.",
]
ECKEN = np.float32([[520, 600], [2450, 450], [2620, 3520], [380, 3330]])


def blatt(breite: int = 1240, hoehe: int = 1754) -> np.ndarray:
    im = Image.new("RGB", (breite, hoehe), "white")
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(FONT), 44) if FONT.exists() else ImageFont.load_default(size=44)
    y = 160
    for z in ZEILEN:
        d.text((120, y), z, fill="black", font=font)
        y += 78
    return np.asarray(im, dtype=np.uint8)


def handyfoto(pfad: Path, unscharf: bool = False, klein: bool = False) -> np.ndarray:
    """Speichert das verzerrte Foto als JPEG und gibt die wahren Ecken zurück."""
    rng = np.random.default_rng(7)
    seite = blatt()
    h, w = 4000, 3000
    bg = np.empty((h, w, 3), np.float32)
    bg[:] = (70, 52, 36)  # Holztisch
    bg += rng.normal(0, 8, bg.shape)

    quelle = np.float32(
        [
            [0, 0],
            [seite.shape[1] - 1, 0],
            [seite.shape[1] - 1, seite.shape[0] - 1],
            [0, seite.shape[0] - 1],
        ]
    )
    m = cv2.getPerspectiveTransform(quelle, ECKEN)
    warp = cv2.warpPerspective(seite, m, (w, h)).astype(np.float32)
    maske = cv2.warpPerspective(np.full(seite.shape[:2], 255, np.uint8), m, (w, h)) > 0
    bild = np.where(maske[..., None], warp, bg)

    bild *= np.array([1.0, 0.90, 0.68], np.float32)  # gelber Farbstich (Kunstlicht)
    schatten = np.linspace(0.55, 1.0, w, dtype=np.float32)[None, :, None]
    bild *= schatten  # Schatten von links (Hand/Handy)
    bild += rng.normal(0, 4, bild.shape)
    out = np.clip(bild, 0, 255).astype(np.uint8)
    ecken = ECKEN.copy()
    if unscharf:
        out = cv2.GaussianBlur(out, (41, 41), 0)
    if klein:
        out = cv2.resize(out, (750, 1000), interpolation=cv2.INTER_AREA)
        ecken = ecken / 4
    Image.fromarray(out).save(pfad, quality=90)
    return ecken
