"""MCP-Werkzeugserver: Akten-Interlock als Werkzeug für KI-Agenten (z. B. F13).

Ein Agent-Orchestrator bindet Werkzeuge per MCP an. Dieser Server stellt die Prüfungen von
Akten-Interlock so bereit, dass ein Sprachmodell sie aufrufen kann, ohne die Sperren
umgehen zu können:

* Die Rolle (``posteingang`` oder ``sachbearbeitung``) wird beim Start festgelegt.
  Kein Werkzeug hat einen Parameter dafür. Ein Modell kann sich nicht selbst zur
  Export-Komponente erklären.
* Es gibt bewusst kein Versandwerkzeug. ``akte_export_vorpruefen`` zeigt nur, ob ein
  Versand die Sperren passieren würde. Versenden bleibt ein Schritt für Menschen im
  Fachverfahren, mit Vier-Augen-Freigabe.
* Der Server hat keinen Freigeber. Was die Policy zur Freigabe vorlegt (HOLD), wird
  hier nie freigegeben: ein Dokument mit unklarer Identität geht in die Quarantäne,
  ein Mensch klärt es später.
* Jeder Zugriff auf eine Akte läuft durch den Prüfkern (``akte_lesen``, ``dokument_*``)
  und landet im hash-verketteten Audit-Protokoll. Ins Protokoll gehen nur IDs, Hashes
  und Prüfergebnisse, nie Namen, Geburtsdaten oder Versicherungsnummern.
* Werkzeugantworten enthalten ebenfalls keine Personendaten der Akte. Das Modell
  erfährt, *dass* ein Merkmal abweicht, nicht *wie* es in der Akte lautet.

``vsnr_pruefen`` und ``fristen_berechnen`` rechnen nur. Sie lesen keine Akte, ändern nichts
und werden nicht protokolliert, damit keine Versicherungsnummer ins Audit gelangt.

Prototyp: Akten werden aus einer JSON-Datei geladen und nur im Speicher gehalten.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from agent_interlock import AuditLog, ToolCall
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__, vsnr
from .dienst import POLICY_STANDARD, AktenInterlock, Status
from .fristen import Antrag, berechne
from .identitaet import Ergebnis
from .modelle import Akte, Dokument, Merkmale

#: Rollen, in denen der Server laufen darf. ``export`` fehlt absichtlich.
ROLLEN = ("posteingang", "sachbearbeitung")

# Formate wie in policies/akte.toml, hier verankert: JSON Schema prüft ``pattern``
# ohne Anker, die Policy mit fullmatch. Ein Test stellt sicher, dass beide gleich bleiben.
AKTE_ID = r"^[A-Z0-9\-]{4,32}$"
DOK_ID = r"^[A-Z0-9\-]{4,40}$"
SHA256 = r"^[0-9a-f]{64}$"
DATUM = r"^\d{4}-\d{2}-\d{2}$"

AkteId = Annotated[str, Field(pattern=AKTE_ID, description="Aktenzeichen, z. B. AK-2025-0001")]


# ---------------------------------------------------------------- Ergebnisse


class Ablage(BaseModel):
    """Ergebnis eines Ablageversuchs."""

    status: Literal["abgelegt", "quarantaene", "abgewiesen", "gesperrt"]
    identitaet: Literal["match", "unklar", "abweichend"] | None = Field(
        description="Ergebnis der Identitätsprüfung, None wenn die Akte unbekannt ist"
    )
    grund: str = Field(description="Begründung ohne Personendaten")
    naechster_schritt: str


class Exportvorpruefung(BaseModel):
    """Würde ein Versand der Akte die Sperren passieren?"""

    akte_id: str
    versandbereit: bool
    fremddokumente: list[str] = Field(description="dok_id von Dokumenten, die nicht passen")
    quarantaene_offen: int
    manifest_vollstaendig: bool
    manifest_sha256: str
    seiten_gesamt: int
    maengel: list[str]
    hinweis: str


class QuarantaeneEintrag(BaseModel):
    dok_id: str
    identitaet: Literal["unklar", "abweichend"]
    gruende: list[str]


class Quarantaeneliste(BaseModel):
    akte_id: str
    eintraege: list[QuarantaeneEintrag]


class VsnrErgebnis(BaseModel):
    gueltig: bool
    fehler: str | None = None


class FristEintrag(BaseModel):
    norm: str
    was: str
    ende: date
    ampel: Literal["gruen", "gelb", "rot", "erledigt"]
    hinweis: str


class Fristen(BaseModel):
    fristen: list[FristEintrag]
    hinweis: str = (
        "Zeigt Fristen und eskaliert. Ob eine Genehmigungsfiktion eingetreten ist, "
        "ist eine Rechtsfrage im Einzelfall und wird hier nicht entschieden."
    )


# ---------------------------------------------------------------- Zustand


@dataclass(slots=True)
class Laufzeit:
    """Ein AktenInterlock plus Lock. MCP führt synchrone Werkzeuge in Worker-Threads aus."""

    dienst: AktenInterlock
    rolle: str
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @contextmanager
    def exklusiv(self) -> Iterator[AktenInterlock]:
        with self._lock:
            yield self.dienst

    def akte_lesen(self, akte_id: str) -> Akte:
        """Gatet jeden Lesezugriff über ``akte_lesen``. Muss unter dem Lock laufen."""
        d = self.dienst.interlock.check(ToolCall(self.rolle, "akte_lesen", {"akte_id": akte_id}))
        if not d.allowed:
            raise ToolError(f"Zugriff gesperrt: {d.reason}")
        akte = self.dienst.akten.get(akte_id)
        if akte is None:
            raise ToolError(f"Akte {akte_id} unbekannt")
        return akte


def lade_akten(pfad: Path) -> list[Akte]:
    """JSON: {"akten": [{"akte_id", "vsnr", "nachname", "vorname", "geburtsdatum", ...}]}"""
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    akten: list[Akte] = []
    for i, a in enumerate(daten.get("akten", [])):
        try:
            v = vsnr.parse(a["vsnr"])
            akten.append(
                Akte(
                    akte_id=a["akte_id"],
                    vsnr=v.text,
                    nachname=a["nachname"],
                    vorname=a["vorname"],
                    geburtsdatum=date.fromisoformat(a["geburtsdatum"]),
                    geburtsname=a.get("geburtsname"),
                )
            )
        except (KeyError, ValueError) as e:
            # Fehler ohne Inhalt melden: die Datei enthält Personendaten.
            grund = type(e).__name__
            raise ValueError(f"Akte Nr. {i + 1} in {pfad.name} ungültig: {grund}") from None
    if not akten:
        raise ValueError(f"{pfad.name} enthält keine Akten")
    return akten


def _pflichtdatum(name: str, wert: str) -> date:
    try:
        return date.fromisoformat(wert)
    except ValueError:
        raise ToolError(f"{name} ist kein gültiges Datum (JJJJ-MM-TT)") from None


def _datum(name: str, wert: str | None) -> date | None:
    return None if wert is None else _pflichtdatum(name, wert)


# ---------------------------------------------------------------- Server


def erstelle_dienst(
    akten: list[Akte],
    audit: AuditLog | None = None,
    policy_pfad: str | Path = POLICY_STANDARD,
    state_pfad: str | Path | None = None,
) -> AktenInterlock:
    """AktenInterlock ohne Freigeber: was die Policy zur Freigabe vorlegt, bleibt gesperrt."""
    dienst = AktenInterlock.erstelle(
        approver=None, audit=audit, policy_pfad=policy_pfad, state_pfad=state_pfad
    )
    for a in akten:
        dienst.akte_anlegen(a)
    return dienst


def baue_server(dienst: AktenInterlock, rolle: str = "posteingang") -> MCPServer:
    if rolle not in ROLLEN:
        raise ValueError(f"Rolle muss eine von {ROLLEN} sein, nicht {rolle!r}")
    if dienst.interlock.approver is not None:
        # Ein Freigeber im Serverprozess könnte vom Modell ausgelöst werden.
        raise ValueError("Der MCP-Server darf keinen Freigeber haben")
    lz = Laufzeit(dienst, rolle)
    dienst.audit.event("mcp_start", rolle=rolle, akten=len(dienst.akten), version=__version__)

    mcp = MCPServer(
        "akten-interlock",
        version=__version__,
        instructions=(
            "Prüfwerkzeuge für die E-Akte. Lege Dokumente nur über dokument_ablegen ab. "
            "Ergebnis 'quarantaene' oder 'abgewiesen' ist endgültig für diesen Lauf: nicht mit "
            "geänderten Merkmalen wiederholen, sondern einen Menschen informieren. "
            "Versand ist kein Werkzeug; akte_export_vorpruefen zeigt nur, ob er möglich wäre."
        ),
    )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Dokument in Akte ablegen",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )
    def dokument_ablegen(
        akte_id: AkteId,
        dok_id: Annotated[str, Field(pattern=DOK_ID, description="Kennung des Dokuments")],
        sha256: Annotated[str, Field(pattern=SHA256, description="SHA-256 der Datei, klein")],
        seiten: Annotated[int, Field(ge=1, le=10_000)],
        eingang: Annotated[str, Field(pattern=DATUM, description="Eingangsdatum JJJJ-MM-TT")],
        quelle: Literal["post", "fax", "egvp", "email", "intern"] = "post",
        format: Literal["pdfa", "pdf", "tiff", "jpeg", "png", "heic", "sonstiges"] = "pdfa",
        vsnr: Annotated[str | None, Field(description="VSNR wie auf dem Dokument")] = None,
        nachname: str | None = None,
        vorname: str | None = None,
        geburtsdatum: Annotated[str | None, Field(pattern=DATUM)] = None,
    ) -> Ablage:
        """Prüft die Merkmale eines Dokuments gegen die Akte und legt es nur bei Übereinstimmung ab.

        Merkmale so übergeben, wie sie auf dem Dokument stehen. Nichts ergänzen oder raten:
        ein fehlendes Merkmal führt zu 'unklar' und damit zu einer Prüfung durch einen Menschen,
        ein geratenes kann ein fremdes Dokument in die falsche Akte bringen.
        """
        merkmale = Merkmale(vsnr, nachname, vorname, _datum("geburtsdatum", geburtsdatum))
        dok = Dokument(
            dok_id=dok_id,
            titel="",  # Titel kann Personendaten enthalten und wird hier nicht gebraucht.
            quelle=quelle,
            eingang=_pflichtdatum("eingang", eingang),
            seiten=seiten,
            sha256=sha256,
            format=format,
            merkmale=merkmale,
            darstellung="pdfa" if format == "pdfa" else "original_beigefuegt",
        )
        with lz.exklusiv() as d:
            lz.akte_lesen(akte_id)
            if any(x.dok_id == dok_id for x in d.akten[akte_id].dokumente):
                raise ToolError(f"{dok_id} liegt bereits in Akte {akte_id}")
            if dok_id in d.quarantaene:
                raise ToolError(f"{dok_id} liegt bereits in der Quarantäne")
            v = d.ablegen(akte_id, dok, agent=lz.rolle)

        identitaet = v.pruefung.ergebnis.value if v.pruefung else None
        schritt = {
            Status.ABGELEGT: "Keiner. Dokument ist in der Akte.",
            Status.QUARANTAENE: (
                "Nicht erneut versuchen. Eine benannte Person klärt die Quarantäne "
                "im Fachverfahren."
            ),
            Status.ABGEWIESEN: "Nicht erneut versuchen. Grund an einen Menschen melden.",
            Status.GESPERRT: "Komponente ist gesperrt. Nur ein Mensch kann die Sperre aufheben.",
        }[v.status]
        return Ablage(
            status=v.status.value,  # type: ignore[arg-type]
            identitaet=identitaet,  # type: ignore[arg-type]
            grund=v.grund,
            naechster_schritt=schritt,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Versand vorprüfen (versendet nichts)", read_only_hint=True, open_world_hint=False
        )
    )
    def akte_export_vorpruefen(akte_id: AkteId) -> Exportvorpruefung:
        """Zeigt, ob ein Versand der Akte die Sperren passieren würde. Versendet nichts.

        Prüft jedes Dokument der Akte erneut (auch Altbestand), zählt offene Quarantäne-Fälle
        und prüft das Manifest. Der Versand selbst ist kein Werkzeug dieses Servers.
        """
        from . import manifest as manifest_mod

        with lz.exklusiv() as d:
            akte = lz.akte_lesen(akte_id)
            fremd = d.endkontrolle(akte)
            offen = d.quarantaene_offen(akte_id)
            m = manifest_mod.erstelle(akte)
        bereit = not fremd and offen == 0 and m.vollstaendig
        return Exportvorpruefung(
            akte_id=akte_id,
            versandbereit=bereit,
            fremddokumente=[x.dok_id for x, _ in fremd],
            quarantaene_offen=offen,
            manifest_vollstaendig=m.vollstaendig,
            manifest_sha256=m.sha256,
            seiten_gesamt=m.seiten_gesamt,
            maengel=list(m.maengel),
            hinweis=(
                "Versand bleibt ein Schritt für Menschen mit Vier-Augen-Freigabe."
                if bereit
                else "Versand würde gesperrt. Fremddokumente und Quarantäne zuerst klären."
            ),
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Quarantäne einer Akte anzeigen", read_only_hint=True, open_world_hint=False
        )
    )
    def quarantaene_auflisten(akte_id: AkteId) -> Quarantaeneliste:
        """Listet Dokumente, die für diese Akte bestimmt waren und in der Quarantäne liegen."""
        with lz.exklusiv() as d:
            lz.akte_lesen(akte_id)
            eintraege = [
                QuarantaeneEintrag(
                    dok_id=dok_id,
                    identitaet=(
                        "abweichend" if q.pruefung.ergebnis is Ergebnis.ABWEICHEND else "unklar"
                    ),
                    gruende=list(q.pruefung.gruende),
                )
                for dok_id, q in d.quarantaene.items()
                if q.ziel_akte == akte_id
            ]
        return Quarantaeneliste(akte_id=akte_id, eintraege=eintraege)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="VSNR prüfen", read_only_hint=True, idempotent_hint=True, open_world_hint=False
        )
    )
    def vsnr_pruefen(nummer: Annotated[str, Field(max_length=40)]) -> VsnrErgebnis:
        """Prüft Format und Prüfziffer einer Versicherungsnummer. Liest keine Akte."""
        try:
            vsnr.parse(nummer)
        except vsnr.VsnrFehler as e:
            return VsnrErgebnis(gueltig=False, fehler=str(e))
        return VsnrErgebnis(gueltig=True)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Fristen nach §§ 14, 18 SGB IX",
            read_only_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    def fristen_berechnen(
        eingang: Annotated[str, Field(pattern=DATUM)],
        heute: Annotated[str, Field(pattern=DATUM)],
        traeger: str = "rentenversicherung",
        gutachten_beauftragt: bool = False,
        gutachten_liegt_vor: Annotated[str | None, Field(pattern=DATUM)] = None,
        begruendete_mitteilung: Annotated[str | None, Field(pattern=DATUM)] = None,
        angekuendigte_entscheidung: Annotated[str | None, Field(pattern=DATUM)] = None,
        entschieden: Annotated[str | None, Field(pattern=DATUM)] = None,
        weitergeleitet: Annotated[str | None, Field(pattern=DATUM)] = None,
    ) -> Fristen:
        """Berechnet Fristen eines Teilhabeantrags mit Ampel. Liest keine Akte, ändert nichts."""
        antrag = Antrag(
            eingang=_pflichtdatum("eingang", eingang),
            traeger=traeger,
            weitergeleitet=_datum("weitergeleitet", weitergeleitet),
            gutachten_beauftragt=gutachten_beauftragt,
            gutachten_liegt_vor=_datum("gutachten_liegt_vor", gutachten_liegt_vor),
            begruendete_mitteilung=_datum("begruendete_mitteilung", begruendete_mitteilung),
            angekuendigte_entscheidung=_datum(
                "angekuendigte_entscheidung", angekuendigte_entscheidung
            ),
            entschieden=_datum("entschieden", entschieden),
        )
        liste = berechne(antrag, _pflichtdatum("heute", heute))
        return Fristen(
            fristen=[
                FristEintrag(
                    norm=f.norm,
                    was=f.was,
                    ende=f.ende,
                    ampel=f.ampel.value,
                    hinweis=f.hinweis,
                )
                for f in liste
            ]
        )

    return mcp
