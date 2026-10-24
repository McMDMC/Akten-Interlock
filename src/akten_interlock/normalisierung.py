"""Eingang und Normalisierung: Datei nach PDF/A, sonst Original beibehalten.

Optional: ocrmypdf (PDF/A + Texterkennung), img2pdf (Bilder verlustfrei nach PDF) und
das Modul ``foto`` (Handyfotos entzerren, Schatten und Farbstich entfernen, HEIC lesen).
Das Original wird immer unverändert unter ``<ziel>/original/`` abgelegt. Scheitert die
Konvertierung, wird das Original beigefügt und so markiert. Es geht nie still etwas verloren.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

BILDFORMATE = {
    ".tif": "tiff",
    ".tiff": "tiff",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".heic": "heic",
    ".heif": "heif",
    ".webp": "webp",
}


@dataclass(frozen=True, slots=True)
class Ergebnis:
    quelle: Path
    ziel: Path
    sha256_original: str
    format: str
    darstellung: str  # "pdfa" | "original_beigefuegt"
    seiten: int
    protokoll: str
    original: Path  # unveränderte Kopie des Eingangs
    maengel: tuple[str, ...] = ()  # z. B. unscharf: Rückfrage an Absender


def sha256_datei(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _seiten(pdf: Path) -> int:
    try:
        import pikepdf

        with pikepdf.open(pdf) as doc:
            return len(doc.pages)
    except Exception:  # noqa: BLE001 - Zählung ist best effort
        return 0


def _ocrmypdf(src: Path, dst: Path, sprache: str, bild: bool = False) -> tuple[bool, str]:
    exe = shutil.which("ocrmypdf")
    if not exe:
        return False, "ocrmypdf nicht installiert"
    extra: list[str] = []
    if bild:
        extra += ["--deskew", "--rotate-pages"]
        if shutil.which("unpaper"):
            extra.append("--clean")
    r = subprocess.run(
        [exe, "--output-type", "pdfa-2", "--skip-text", *extra, "-l", sprache, str(src), str(dst)],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        letzte = (r.stderr.strip().splitlines() or ["unbekannter Fehler"])[-1]
        return False, f"ocrmypdf Exit {r.returncode}: {letzte}"
    return True, "PDF/A-2 erzeugt (ocrmypdf)"


def _foto_aufbereiten(src: Path, ordner: Path, schritte: list[str]) -> tuple[Path, tuple[str, ...]]:
    try:
        from . import foto
    except ImportError:
        schritte.append("Foto-Aufbereitung nicht installiert (Extra 'foto')")
        return src, ()
    try:
        r = foto.bereite_auf(src, ordner)
    except Exception as e:  # noqa: BLE001 - Aufbereitung ist ein Zusatz, kein Muss
        schritte.append(f"Foto-Aufbereitung fehlgeschlagen: {e}")
        return src, ()
    schritte.extend(r.schritte)
    return r.ziel, r.qualitaet.maengel


def normalisiere(
    src: Path, zielordner: Path, sprache: str = "deu", foto_aufbereitung: bool = True
) -> Ergebnis:
    zielordner.mkdir(parents=True, exist_ok=True)
    (zielordner / "original").mkdir(exist_ok=True)
    original = zielordner / "original" / src.name
    shutil.copy2(src, original)
    sha = sha256_datei(src)
    endung = src.suffix.lower()
    fmt = "pdf" if endung == ".pdf" else BILDFORMATE.get(endung, endung.lstrip(".") or "unbekannt")
    ziel = zielordner / (src.stem + ".pdfa.pdf")
    schritte: list[str] = [f"Original gesichert, SHA-256 {sha[:12]}…"]
    maengel: tuple[str, ...] = ()

    eingabe: Path | None = src if endung == ".pdf" else None
    if endung in BILDFORMATE:
        bild = src
        if foto_aufbereitung:
            bild, maengel = _foto_aufbereiten(src, zielordner, schritte)
        try:
            import img2pdf

            zwischen = zielordner / (src.stem + ".img.pdf")
            zwischen.write_bytes(img2pdf.convert(str(bild)))
            eingabe = zwischen
            schritte.append("Bild verlustfrei nach PDF (img2pdf)")
        except Exception as e:  # noqa: BLE001
            schritte.append(f"img2pdf fehlgeschlagen: {e}")

    if eingabe is not None:
        ok, msg = _ocrmypdf(eingabe, ziel, sprache, bild=endung in BILDFORMATE)
        schritte.append(msg)
        if ok:
            if eingabe != src:
                eingabe.unlink(missing_ok=True)  # Zwischen-PDF wird nicht mehr gebraucht
            return Ergebnis(
                src, ziel, sha, fmt, "pdfa", _seiten(ziel), "; ".join(schritte), original, maengel
            )

    # Fallback: Original unverändert beifügen (§ 3 Abs. 3 BehAktÜbV).
    seiten = _seiten(src) if endung == ".pdf" else 1
    schritte.append("Original beigefügt")
    return Ergebnis(
        src,
        original,
        sha,
        fmt,
        "original_beigefuegt",
        seiten,
        "; ".join(schritte),
        original,
        maengel,
    )
