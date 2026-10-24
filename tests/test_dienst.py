from __future__ import annotations

from collections.abc import Callable
from datetime import date

from agent_interlock import ToolCall, Verdict, verify

from akten_interlock.dienst import AktenInterlock, Status
from akten_interlock.fristen import Antrag
from akten_interlock.modelle import Akte, Merkmale

from .conftest import Votes, dok

Make = Callable[..., tuple[AktenInterlock, Votes]]


def test_match_wird_abgelegt(make_dienst: Make, akte: Akte) -> None:
    d, votes = make_dienst()
    v = d.ablegen(akte.akte_id, dok(akte.merkmale))
    assert v.status is Status.ABGELEGT
    assert votes == {}  # keine Freigabe nötig
    assert len(akte.dokumente) == 1


def test_unklar_braucht_freigabe(make_dienst: Make, akte: Akte) -> None:
    d, votes = make_dienst()
    v = d.ablegen(akte.akte_id, dok(Merkmale(None, "Muster", "Erika", akte.geburtsdatum)))
    assert v.status is Status.ABGELEGT
    assert votes == {"sb": True}


def test_unklar_abgelehnt_geht_in_quarantaene(make_dienst: Make, akte: Akte) -> None:
    d, _ = make_dienst(ablage=False)
    x = dok(Merkmale(None, "Muster", "Erika", akte.geburtsdatum))
    v = d.ablegen(akte.akte_id, x)
    assert v.status is Status.QUARANTAENE
    assert x.dok_id in d.quarantaene
    assert akte.dokumente == []


def test_fremddokument_nie_in_akte(make_dienst: Make, akte: Akte, fremd: Merkmale) -> None:
    d, votes = make_dienst()
    v = d.ablegen(akte.akte_id, dok(fremd))
    assert v.status is Status.QUARANTAENE
    assert akte.dokumente == []
    assert votes == {}  # kein Mensch kann es "durchwinken"


def test_bot_wird_nach_drei_verstoessen_gesperrt(make_dienst: Make, akte: Akte) -> None:
    d, _ = make_dienst()
    verdicts = []
    for i in range(4):
        r = d.interlock.check(
            ToolCall(
                "posteingang",
                "dokument_ablegen",
                {
                    "akte_id": akte.akte_id,
                    "dok_id": f"DOK-99{i:02d}",
                    "identitaet": "abweichend",
                    "sha256": "a" * 64,
                },
            )
        )
        verdicts.append(r.verdict)
    assert verdicts == [Verdict.DENY] * 3 + [Verdict.LOCKOUT]
    # Auch korrekte Ablagen sind jetzt gesperrt, bis eine Person freigibt.
    assert d.ablegen(akte.akte_id, dok(akte.merkmale)).status is Status.GESPERRT
    assert d.interlock.release("posteingang", operator="IT-Betrieb")
    assert d.ablegen(akte.akte_id, dok(akte.merkmale)).status is Status.ABGELEGT


def test_unbekannter_agent_darf_nicht_exportieren(make_dienst: Make, akte: Akte) -> None:
    d, _ = make_dienst()
    d.ablegen(akte.akte_id, dok(akte.merkmale))
    v = d.exportieren(akte.akte_id, "sozialgericht", agent="posteingang")
    assert v.status is Status.ABGEWIESEN


def test_export_sperrt_bei_altbestand(make_dienst: Make, akte: Akte, fremd: Merkmale) -> None:
    d, votes = make_dienst()
    d.ablegen(akte.akte_id, dok(akte.merkmale))
    alt = dok(fremd)
    akte.dokumente.append(alt)  # lag schon vor Einführung in der Akte
    v = d.exportieren(akte.akte_id, "sozialgericht")
    assert v.status is Status.ABGEWIESEN
    assert "fremd_treffer" in v.grund and alt.dok_id in v.grund
    assert votes == {}  # vor der Freigabe abgefangen

    assert (
        d.aus_akte_in_quarantaene(akte.akte_id, alt.dok_id, "S. Pruefer").status
        is Status.QUARANTAENE
    )
    v = d.exportieren(akte.akte_id, "sozialgericht")
    assert "quarantaene_offen" in v.grund  # erst klären, dann versenden

    d.quarantaene_klaeren(alt.dok_id, "S. Pruefer", "an zuständige Stelle zurück")
    v = d.exportieren(akte.akte_id, "sozialgericht")
    assert v.status is Status.VERSANDT
    assert votes == {"eins": True, "zwei": True}


def test_vier_augen_ein_nein_reicht(make_dienst: Make, akte: Akte) -> None:
    d, votes = make_dienst(zweite=False)
    d.ablegen(akte.akte_id, dok(akte.merkmale))
    v = d.exportieren(akte.akte_id, "sozialgericht")
    assert v.status is Status.ABGEWIESEN
    assert v.grund == "rejected by operator"


def test_unvollstaendiges_manifest_sperrt(make_dienst: Make, akte: Akte) -> None:
    d, _ = make_dienst()
    d.ablegen(akte.akte_id, dok(akte.merkmale))
    kaputt = dok(akte.merkmale, seiten=0)
    akte.dokumente.append(kaputt)
    v = d.exportieren(akte.akte_id, "sozialgericht")
    assert v.status is Status.ABGEWIESEN
    assert v.manifest is not None and not v.manifest.vollstaendig


def test_not_halt(make_dienst: Make, akte: Akte) -> None:
    d, _ = make_dienst()
    d.ablegen(akte.akte_id, dok(akte.merkmale))
    d.datenschutzvorfall("Betroffene Person", "Fremddaten gemeldet")
    assert d.exportieren(akte.akte_id, "sozialgericht").status is Status.GESPERRT
    # Ablage (write) läuft weiter, nur der Versand ist gestoppt.
    assert d.ablegen(akte.akte_id, dok(akte.merkmale)).status is Status.ABGELEGT
    d.not_halt_zuruecksetzen("Datenschutzbeauftragte")
    assert d.exportieren(akte.akte_id, "sozialgericht").status is Status.VERSANDT


def test_fristen_eskalieren_ins_audit(make_dienst: Make, akte: Akte) -> None:
    d, _ = make_dienst()
    d.fristen_pruefen(akte.akte_id, Antrag(eingang=date(2025, 3, 3)), date(2025, 6, 10))
    esk = [r for r in d.audit.records if r.get("tool") == "frist_eskalieren"]
    assert len(esk) == 3 and all(r["verdict"] == "allow" for r in esk)


def test_audit_kette_und_freigabe_namen(make_dienst: Make, akte: Akte) -> None:
    d, _ = make_dienst()
    d.ablegen(akte.akte_id, dok(akte.merkmale))
    d.exportieren(akte.akte_id, "sozialgericht")
    recs = d.audit.records
    assert verify(recs) == (True, None)
    namen = {r["person"] for r in recs if r["kind"] == "freigabe"}
    assert namen == {"eins", "zwei"}
    # Kein Gesundheitsinhalt im Protokoll, nur Metadaten.
    assert all("titel" not in str(r.get("args", {})) for r in recs)
