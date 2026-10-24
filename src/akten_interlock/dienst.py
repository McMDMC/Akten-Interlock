"""AktenInterlock: die Fassade vor der E-Akte.

Drei Sperren an drei Übergängen:
1. Eingang -> Akte:  Identitätsprüfung + Policy (dokument_ablegen / _nach_pruefung / quarantaene)
2. Akte -> außen:    Endkontrolle aller Dokumente + Manifest + Vier-Augen (akte_exportieren)
3. Zeit:             Fristenwächter mit Andon-Eskalation (frist_eskalieren)
Dazu der Not-Halt: ein gemeldeter Datenschutzvorfall sperrt jeden Versand.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path

from agent_interlock import AuditLog, Decision, Interlock, Policy, ToolCall, Verdict

from . import fristen as fristen_mod
from . import manifest as manifest_mod
from .identitaet import Ergebnis, Pruefung, pruefe
from .modelle import Akte, Dokument

POLICY_STANDARD = Path(__file__).resolve().parent / "policies" / "akte.toml"


class Status(StrEnum):
    ABGELEGT = "abgelegt"
    QUARANTAENE = "quarantaene"
    ABGEWIESEN = "abgewiesen"
    GESPERRT = "gesperrt"
    VERSANDT = "versandt"


@dataclass(frozen=True, slots=True)
class Vorgang:
    status: Status
    grund: str
    entscheidung: Decision | None = None
    pruefung: Pruefung | None = None
    manifest: manifest_mod.Manifest | None = None


@dataclass(slots=True)
class Quarantaene:
    dokument: Dokument
    ziel_akte: str
    pruefung: Pruefung


@dataclass(slots=True)
class AktenInterlock:
    interlock: Interlock
    akten: dict[str, Akte] = field(default_factory=dict)
    quarantaene: dict[str, Quarantaene] = field(default_factory=dict)
    versand: Callable[[Akte, manifest_mod.Manifest, str], None] | None = None

    @classmethod
    def erstelle(
        cls,
        approver: Callable[[ToolCall], bool] | None = None,
        audit: AuditLog | None = None,
        policy_pfad: str | Path = POLICY_STANDARD,
        state_pfad: str | Path | None = None,
    ) -> AktenInterlock:
        il = Interlock(
            Policy.from_file(policy_pfad),
            audit=audit or AuditLog(),
            approver=approver,
            state_path=state_pfad,
        )
        return cls(il)

    @property
    def audit(self) -> AuditLog:
        return self.interlock.audit

    def akte_anlegen(self, akte: Akte) -> None:
        self.akten[akte.akte_id] = akte

    # ------------------------------------------------------------ 1. Ablage

    def ablegen(self, akte_id: str, dok: Dokument, agent: str = "posteingang") -> Vorgang:
        akte = self.akten.get(akte_id)
        if akte is None:
            return Vorgang(Status.ABGEWIESEN, f"Akte {akte_id} unbekannt")
        p = pruefe(akte, dok.merkmale)
        basis = {
            "akte_id": akte_id,
            "dok_id": dok.dok_id,
            "identitaet": p.ergebnis.value,
            "sha256": dok.sha256,
        }

        if p.ergebnis is Ergebnis.ABWEICHEND:
            return self._in_quarantaene(akte_id, dok, p, agent)

        tool = (
            "dokument_ablegen" if p.ergebnis is Ergebnis.MATCH else "dokument_ablegen_nach_pruefung"
        )
        d = self.interlock.check(ToolCall(agent, tool, basis))
        if d.allowed:
            akte.dokumente.append(dok)
            return Vorgang(Status.ABGELEGT, p.text, d, p)
        if d.verdict is Verdict.LOCKOUT:
            return Vorgang(Status.GESPERRT, d.reason, d, p)
        if p.ergebnis is Ergebnis.UNKLAR:
            return self._in_quarantaene(akte_id, dok, p, agent, vorher=d)
        return Vorgang(Status.ABGEWIESEN, d.reason, d, p)

    def _in_quarantaene(
        self, akte_id: str, dok: Dokument, p: Pruefung, agent: str, vorher: Decision | None = None
    ) -> Vorgang:
        grund = p.text[:300]
        d = self.interlock.check(
            ToolCall(
                agent,
                "dokument_quarantaene",
                {"dok_id": dok.dok_id, "identitaet": p.ergebnis.value, "grund": grund},
            )
        )
        if not d.allowed:
            # Auch die Quarantäne gesperrt: Dokument bleibt im Eingang, nichts in der Akte.
            return Vorgang(Status.GESPERRT, d.reason, d, p)
        self.quarantaene[dok.dok_id] = Quarantaene(dok, akte_id, p)
        return Vorgang(Status.QUARANTAENE, grund, vorher or d, p)

    def aus_akte_in_quarantaene(
        self, akte_id: str, dok_id: str, person: str, agent: str = "sachbearbeitung"
    ) -> Vorgang:
        """Eindämmung: ein bereits abgelegtes Fremddokument aus der Akte nehmen."""
        akte = self.akten[akte_id]
        dok = next((d for d in akte.dokumente if d.dok_id == dok_id), None)
        if dok is None:
            return Vorgang(Status.ABGEWIESEN, f"{dok_id} nicht in Akte {akte_id}")
        if not person:
            raise ValueError("Eindämmung braucht eine namentlich benannte Person")
        p = pruefe(akte, dok.merkmale)
        v = self._in_quarantaene(
            akte_id,
            dok,
            p
            if p.ergebnis is not Ergebnis.MATCH
            else Pruefung(Ergebnis.UNKLAR, ("manuell markiert",)),
            agent,
        )
        if v.status is Status.QUARANTAENE:
            akte.dokumente.remove(dok)
            self.audit.event("eindaemmung", akte_id=akte_id, dok_id=dok_id, person=person)
        return v

    def quarantaene_klaeren(self, dok_id: str, person: str, ergebnis: str) -> None:
        """Abschluss durch eine benannte Person, z. B. 'an zuständige Stelle zurück'."""
        if not person:
            raise ValueError("Klärung braucht eine namentlich benannte Person")
        q = self.quarantaene.pop(dok_id)
        self.audit.event(
            "quarantaene_geklaert",
            dok_id=dok_id,
            akte_id=q.ziel_akte,
            person=person,
            ergebnis=ergebnis[:200],
        )

    def quarantaene_offen(self, akte_id: str) -> int:
        return sum(1 for q in self.quarantaene.values() if q.ziel_akte == akte_id)

    # ------------------------------------------------------------ 2. Export

    def endkontrolle(self, akte: Akte) -> list[tuple[Dokument, Pruefung]]:
        """Prüft jedes Dokument der Akte erneut, auch Altbestand vor Einführung."""
        return [
            (d, p)
            for d in akte.dokumente
            if (p := pruefe(akte, d.merkmale)).ergebnis is Ergebnis.ABWEICHEND
        ]

    def exportieren(self, akte_id: str, empfaenger: str, agent: str = "export") -> Vorgang:
        akte = self.akten.get(akte_id)
        if akte is None:
            return Vorgang(Status.ABGEWIESEN, f"Akte {akte_id} unbekannt")
        fremd = self.endkontrolle(akte)
        m = manifest_mod.erstelle(akte)
        args = {
            "akte_id": akte_id,
            "empfaenger": empfaenger,
            "fremd_treffer": len(fremd),
            "quarantaene_offen": self.quarantaene_offen(akte_id),
            "manifest_vollstaendig": m.vollstaendig,
            "manifest_sha256": m.sha256,
        }
        d = self.interlock.check(ToolCall(agent, "akte_exportieren", args))
        if not d.allowed:
            grund = d.reason
            if fremd:
                grund += " | Fremddokumente: " + ", ".join(
                    f"{x.dok_id} ({p.text})" for x, p in fremd
                )
            status = Status.GESPERRT if d.verdict is Verdict.LOCKOUT else Status.ABGEWIESEN
            return Vorgang(status, grund, d, manifest=m)
        if self.versand:
            self.versand(akte, m, empfaenger)
        self.audit.event(
            "versand",
            akte_id=akte_id,
            empfaenger=empfaenger,
            manifest=m.sha256,
            seiten=m.seiten_gesamt,
        )
        return Vorgang(Status.VERSANDT, "freigegeben", d, manifest=m)

    # ------------------------------------------------------------ Not-Halt

    def datenschutzvorfall(self, gemeldet_von: str, grund: str) -> None:
        """Andon: stoppt jeden Versand nach außen, bis eine benannte Person zurücksetzt."""
        self.interlock.estop(gemeldet_von, grund)

    def not_halt_zuruecksetzen(self, person: str) -> None:
        self.interlock.reset_estop(person)

    # ------------------------------------------------------------ 3. Fristen

    def fristen_pruefen(
        self, akte_id: str, antrag: fristen_mod.Antrag, heute: date, agent: str = "fristenwaechter"
    ) -> list[fristen_mod.Frist]:
        liste = fristen_mod.berechne(antrag, heute)
        for f in liste:
            if f.ampel in (fristen_mod.Ampel.GELB, fristen_mod.Ampel.ROT):
                self.interlock.check(
                    ToolCall(
                        agent,
                        "frist_eskalieren",
                        {
                            "akte_id": akte_id,
                            "norm": f.norm[:40],
                            "ende": f.ende.isoformat(),
                            "ampel": f.ampel.value,
                        },
                    )
                )
        return liste
