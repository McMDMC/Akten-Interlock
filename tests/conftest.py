from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from agent_interlock import AuditLog, ToolCall

from akten_interlock import vsnr
from akten_interlock.dienst import AktenInterlock
from akten_interlock.freigabe import EinePerson, NachVorgang, Person, VierAugen
from akten_interlock.modelle import Akte, Dokument, Merkmale

GEB_A = date(1985, 3, 15)
GEB_B = date(1979, 7, 2)


@pytest.fixture
def akte() -> Akte:
    return Akte("AK-TEST-0001", vsnr.erzeuge("12", GEB_A, "Muster"), "Muster", "Erika", GEB_A)


@pytest.fixture
def fremd() -> Merkmale:
    return Merkmale(vsnr.erzeuge("53", GEB_B, "Beispiel"), "Beispiel", "Max", GEB_B)


_n = iter(range(1, 10_000))


def dok(m: Merkmale, seiten: int = 2, fmt: str = "pdfa", darstellung: str = "pdfa") -> Dokument:
    n = next(_n)
    return Dokument(
        f"DOK-{n:04d}",
        f"Dokument {n}",
        "post",
        date(2025, 3, 3),
        seiten,
        f"{n:064x}",
        fmt,
        m,
        darstellung,
    )


Votes = dict[str, bool]


@pytest.fixture
def make_dienst(akte: Akte) -> Callable[..., tuple[AktenInterlock, Votes]]:
    def make(
        ablage: bool = True, erste: bool = True, zweite: bool = True
    ) -> tuple[AktenInterlock, Votes]:
        audit = AuditLog()
        votes: Votes = {}

        def p(name: str, ok: bool) -> Person:
            def f(c: ToolCall) -> bool:
                votes[name] = ok
                return ok

            return Person(name, f)

        approver = NachVorgang(
            {
                "dokument_ablegen_nach_pruefung": EinePerson(p("sb", ablage), audit),
                "akte_exportieren": VierAugen(p("eins", erste), p("zwei", zweite), audit),
            },
            audit,
        )
        d = AktenInterlock.erstelle(approver=approver, audit=audit)
        d.akte_anlegen(akte)
        return d, votes

    return make
