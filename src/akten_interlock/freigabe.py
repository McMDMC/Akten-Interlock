"""Freigaben (Mensch in der Schleife) für Vorgänge im Status HOLD.

agent-interlock ruft für HOLD einen Approver ``(ToolCall) -> bool`` auf. Hier:
* EinePerson: eine namentliche Freigabe (z. B. Ablage nach manueller Identitätsprüfung).
* VierAugen: zwei verschiedene Personen müssen zustimmen (Versand nach außen).
* NachVorgang: wählt die Freigabe-Regel je Tool. Unbekanntes Tool -> abgelehnt.

Jede Einzelentscheidung landet mit Namen im Audit-Protokoll.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from agent_interlock import AuditLog, ToolCall

Entscheider = Callable[[ToolCall], bool]


@dataclass(frozen=True, slots=True)
class Person:
    name: str
    entscheidet: Entscheider


class EinePerson:
    def __init__(self, person: Person, audit: AuditLog) -> None:
        if not person.name:
            raise ValueError("Freigabe braucht eine namentlich benannte Person")
        self.person, self.audit = person, audit

    def __call__(self, call: ToolCall) -> bool:
        ok = bool(self.person.entscheidet(call))
        self.audit.event("freigabe", tool=call.tool, person=self.person.name, zugestimmt=ok)
        return ok


class VierAugen:
    def __init__(self, erste: Person, zweite: Person, audit: AuditLog) -> None:
        if not erste.name or not zweite.name or erste.name == zweite.name:
            raise ValueError("Vier-Augen-Prinzip braucht zwei verschiedene benannte Personen")
        self.erste, self.zweite, self.audit = erste, zweite, audit

    def __call__(self, call: ToolCall) -> bool:
        for p in (self.erste, self.zweite):
            ok = bool(p.entscheidet(call))
            self.audit.event("freigabe", tool=call.tool, person=p.name, zugestimmt=ok)
            if not ok:
                return False
        return True


class NachVorgang:
    def __init__(self, regeln: Mapping[str, Callable[[ToolCall], bool]], audit: AuditLog) -> None:
        self.regeln, self.audit = dict(regeln), audit

    def __call__(self, call: ToolCall) -> bool:
        regel = self.regeln.get(call.tool)
        if regel is None:
            self.audit.event(
                "freigabe",
                tool=call.tool,
                person=None,
                zugestimmt=False,
                grund="keine Freigaberegel für diesen Vorgang",
            )
            return False
        return regel(call)


def konsole(name: str) -> Person:
    """Interaktive Freigabe im Terminal."""

    def frage(call: ToolCall) -> bool:
        print(f"\n[{name}] Freigabe für {call.tool}: {call.args}")
        return input("Freigeben? (ja/nein) ").strip().lower() == "ja"

    return Person(name, frage)
