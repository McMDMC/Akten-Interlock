"""Demo mit rein synthetischen Daten: der Ausgangsfall, diesmal mit Sperren.

Alle Namen, Nummern und Daten sind erfunden. Die VSNR werden mit gültiger
Prüfziffer erzeugt, gehören aber zu niemandem.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from agent_interlock import AuditLog, ToolCall, verify

from . import vsnr
from .dienst import AktenInterlock
from .freigabe import EinePerson, NachVorgang, Person, VierAugen
from .fristen import Ampel, Antrag
from .modelle import Akte, Dokument, Merkmale

FAKE_HASH = "{:064x}"


def _dok(
    n: int,
    titel: str,
    quelle: str,
    eingang: date,
    seiten: int,
    m: Merkmale,
    fmt: str = "pdfa",
    darstellung: str = "pdfa",
) -> Dokument:
    return Dokument(
        f"DOK-{n:04d}", titel, quelle, eingang, seiten, FAKE_HASH.format(n), fmt, m, darstellung
    )


def baue(audit_pfad: Path | None = None) -> tuple[AktenInterlock, dict[str, object]]:
    audit = AuditLog(audit_pfad)
    ja = Person("S. Pruefer (Sachbearbeitung)", lambda c: True)
    ja2 = Person("T. Zweitsicht (Teamleitung)", lambda c: True)
    approver = NachVorgang(
        {
            "dokument_ablegen_nach_pruefung": EinePerson(ja, audit),
            "akte_exportieren": VierAugen(ja, ja2, audit),
        },
        audit,
    )
    ai = AktenInterlock.erstelle(approver=approver, audit=audit)

    geb_a, geb_b = date(1985, 3, 15), date(1979, 7, 2)
    a = Akte("AK-2025-0001", vsnr.erzeuge("12", geb_a, "Muster"), "Muster", "Erika", geb_a)
    ai.akte_anlegen(a)
    fremd = Merkmale(vsnr.erzeuge("53", geb_b, "Beispiel"), "Beispiel", "Max", geb_b)
    return ai, {"a": a, "fremd": fremd}


def zeile(schritt: str, ergebnis: str, grund: str) -> None:
    print(f"  {schritt:<46} {ergebnis:<12} {grund}")


def lauf(audit_pfad: Path | None = None) -> int:
    ai, ctx = baue(audit_pfad)
    a: Akte = ctx["a"]  # type: ignore[assignment]
    fremd: Merkmale = ctx["fremd"]  # type: ignore[assignment]
    eigen = a.merkmale

    print("\n1) Eingang -> Akte (Identitätsprüfung + Policy)")
    v = ai.ablegen(
        a.akte_id, _dok(1, "Entlassungsbericht Reha", "post", date(2025, 2, 10), 14, eigen)
    )
    zeile("Entlassungsbericht, alle Merkmale passen", v.status, v.grund)
    v = ai.ablegen(
        a.akte_id,
        _dok(
            2,
            "LTA-Antrag (Fax, ohne VSNR)",
            "fax",
            date(2025, 3, 3),
            4,
            Merkmale(None, "Muster", "Erika", a.geburtsdatum),
        ),
    )
    zeile("LTA-Antrag per Fax, VSNR fehlt -> HOLD", v.status, v.grund)
    v = ai.ablegen(
        a.akte_id, _dok(3, "Psychotherap. Stellungnahme", "post", date(2025, 4, 2), 3, fremd)
    )
    zeile("Stellungnahme einer FREMDEN Person", v.status, v.grund)

    print("\n2) Fehlkonfigurierter Ablage-Bot umgeht die Prüfung")
    for i in range(4):
        d = ai.interlock.check(
            ToolCall(
                "posteingang",
                "dokument_ablegen",
                {
                    "akte_id": a.akte_id,
                    "dok_id": f"DOK-09{i:02d}",
                    "identitaet": "abweichend",
                    "sha256": FAKE_HASH.format(900 + i),
                },
            )
        )
        zeile(f"Direktaufruf {i + 1}", d.verdict, d.reason)
    ai.interlock.release("posteingang", operator="IT-Betrieb (R. Admin)")
    zeile("Freigabe der Sperre durch benannte Person", "release", "IT-Betrieb (R. Admin)")

    print("\n3) Altbestand: Fremddokument lag schon vor Einführung in der Akte")
    a.dokumente.append(
        _dok(4, "Psychotherap. Stellungnahme (Altbestand)", "intern", date(2025, 5, 5), 2, fremd)
    )
    a.dokumente.append(
        _dok(
            5,
            "Röntgenbild (DICOM)",
            "post",
            date(2025, 5, 20),
            1,
            eigen,
            fmt="dicom",
            darstellung="original_beigefuegt",
        )
    )
    v = ai.exportieren(a.akte_id, "sozialgericht")
    zeile("Export an Sozialgericht", v.status, v.grund)

    print("\n4) Eindämmung und Klärung, dann Versand mit Vier-Augen-Freigabe")
    v = ai.aus_akte_in_quarantaene(a.akte_id, "DOK-0004", "S. Pruefer (Sachbearbeitung)")
    zeile("DOK-0004 aus der Akte in Quarantäne", v.status, v.grund)
    for dok_id in list(ai.quarantaene):
        ai.quarantaene_klaeren(
            dok_id,
            "S. Pruefer (Sachbearbeitung)",
            "an zuständige Stelle zurück, Art. 33/34 DSGVO geprüft",
        )
    zeile("Quarantäne geklärt (2 Dokumente)", "ok", "")
    v = ai.exportieren(a.akte_id, "sozialgericht")
    zeile("Export an Sozialgericht", v.status, v.grund)
    if v.manifest:
        print("\n" + "\n".join("     " + z for z in v.manifest.als_text().splitlines()))

    print("\n5) Not-Halt bei Datenschutzvorfall")
    ai.datenschutzvorfall("Hinweis betroffene Person", "Fremddaten in versandter Akte gemeldet")
    v = ai.exportieren(a.akte_id, "bevollmaechtigte")
    zeile("Export an Bevollmächtigte während Not-Halt", v.status, v.grund)
    ai.not_halt_zuruecksetzen("Datenschutzbeauftragte (D. Schutz)")
    v = ai.exportieren(a.akte_id, "bevollmaechtigte")
    zeile("Export nach Rücksetzen durch DSB", v.status, v.grund)

    print("\n6) Fristenwächter (Fax-Antrag vom 03.03.2025, Stand 10.06.2025)")
    for f in ai.fristen_pruefen(a.akte_id, Antrag(eingang=date(2025, 3, 3)), date(2025, 6, 10)):
        mark = {Ampel.ROT: "ROT", Ampel.GELB: "GELB"}.get(f.ampel, f.ampel.value)
        zeile(f"{f.norm}: {f.was}"[:46], mark, f"Ende {f.ende:%d.%m.%Y} {f.hinweis}")

    print("\n7) Audit-Protokoll")
    recs = (
        ai.audit.records
        if audit_pfad is None
        else [json.loads(z) for z in audit_pfad.read_text().splitlines() if z.strip()]
    )
    ok, _ = verify(recs)
    zeile(f"{len(recs)} Einträge, Hash-Kette", "intakt" if ok else "GEBROCHEN", "")
    manipuliert = [dict(r) for r in recs]
    manipuliert[5]["reason"] = "nachträglich geändert"
    ok2, idx = verify(manipuliert)
    zeile("Eintrag 5 nachträglich verändert", "intakt" if ok2 else "GEBROCHEN", f"ab Eintrag {idx}")
    return 0
