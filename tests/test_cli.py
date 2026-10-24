from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from akten_interlock.cli import main


def test_vsnr(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vsnr", "65 170839 J 003"]) == 0
    assert "gültig" in capsys.readouterr().out
    assert main(["vsnr", "65170839J004"]) == 1


def test_fristen(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["fristen", "--eingang", "2025-03-03", "--heute", "2025-06-10"]) == 0
    out = capsys.readouterr().out
    assert out.count("rot") == 3 and "Genehmigungsfiktion" in out


def test_datei_fehlt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["normalisiere", str(tmp_path / "gibtsnicht.jpg")]) == 1
    assert "nicht gefunden" in capsys.readouterr().out


def test_demo_und_audit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "audit.jsonl"
    assert main(["demo", "--audit", str(log)]) == 0
    assert main(["audit-pruefen", str(log)]) == 0
    zeilen = log.read_text().splitlines()
    rec = json.loads(zeilen[3])
    rec["reason"] = "manipuliert"
    zeilen[3] = json.dumps(rec)
    log.write_text("\n".join(zeilen) + "\n")
    assert main(["audit-pruefen", str(log)]) == 2


@pytest.mark.skipif(
    not (shutil.which("ocrmypdf") and shutil.which("gs")), reason="ocrmypdf mit Ghostscript fehlt"
)
def test_normalisiere_handyfoto(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("cv2")
    from .testbild import handyfoto

    foto = tmp_path / "antrag.jpg"
    handyfoto(foto, unscharf=True)
    assert main(["normalisiere", str(foto), "--ziel", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("pdfa:")
    assert "Perspektive entzerrt" in out
    assert "Rückfrage an Absender empfohlen: unscharf" in out
    assert (tmp_path / "out" / "original" / "antrag.jpg").read_bytes() == foto.read_bytes()
    assert not (tmp_path / "out" / "antrag.img.pdf").exists()
