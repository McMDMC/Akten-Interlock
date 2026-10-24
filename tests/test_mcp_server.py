"""MCP-Server: Sperren, Datensparsamkeit und Schema-Gleichlauf mit der Policy."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

import anyio  # noqa: E402
from agent_interlock import AuditLog, Policy  # noqa: E402
from mcp import Client  # noqa: E402

from akten_interlock import mcp_server, vsnr  # noqa: E402
from akten_interlock.cli import main  # noqa: E402
from akten_interlock.dienst import POLICY_STANDARD, AktenInterlock  # noqa: E402
from akten_interlock.modelle import Akte  # noqa: E402

pytestmark = pytest.mark.anyio

GEB_A, GEB_B = date(1985, 3, 15), date(1979, 7, 2)
VSNR_A = vsnr.erzeuge("12", GEB_A, "Muster")
VSNR_B = vsnr.erzeuge("53", GEB_B, "Beispiel")
AKTE = "AK-2025-0001"
PERSONENDATEN = ("Muster", "Erika", VSNR_A, "1985-03-15", "Beispiel", VSNR_B, "1979-07-02")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def dienst() -> AktenInterlock:
    akte = Akte(AKTE, VSNR_A, "Muster", "Erika", GEB_A)
    return mcp_server.erstelle_dienst([akte], audit=AuditLog())


_n = iter(range(1, 100_000))


def dok_args(**merkmale: Any) -> dict[str, Any]:
    n = next(_n)
    return {
        "akte_id": AKTE,
        "dok_id": f"DOK-{n:04d}",
        "sha256": f"{n:064x}",
        "seiten": 2,
        "eingang": "2025-03-03",
        **merkmale,
    }


PASSEND = {"vsnr": VSNR_A, "nachname": "Muster", "geburtsdatum": "1985-03-15"}
FREMD = {"vsnr": VSNR_B, "nachname": "Beispiel", "geburtsdatum": "1979-07-02"}


async def rufe(dienst: AktenInterlock, tool: str, args: dict[str, Any]) -> Any:
    async with Client(mcp_server.baue_server(dienst)) as c:
        return await c.call_tool(tool, args)


# ------------------------------------------------------------ Werkzeugfläche


async def test_kein_versand_und_keine_rollenwahl(dienst: AktenInterlock) -> None:
    async with Client(mcp_server.baue_server(dienst)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == {
        "dokument_ablegen",
        "akte_export_vorpruefen",
        "quarantaene_auflisten",
        "vsnr_pruefen",
        "fristen_berechnen",
    }
    for t in tools.values():
        props = set(t.input_schema.get("properties", {}))
        assert not props & {"agent", "rolle", "person", "freigabe"}, t.name
    assert tools["akte_export_vorpruefen"].annotations is not None
    assert tools["akte_export_vorpruefen"].annotations.read_only_hint is True


async def test_schema_entspricht_policy(dienst: AktenInterlock) -> None:
    """Die Muster im Werkzeug-Schema dürfen nicht von der Policy wegdriften."""
    policy = Policy.from_file(POLICY_STANDARD).tools["dokument_ablegen"].params
    async with Client(mcp_server.baue_server(dienst)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    props = tools["dokument_ablegen"].input_schema["properties"]
    for name in ("akte_id", "dok_id", "sha256"):
        regel = policy[name].pattern
        assert regel is not None
        assert props[name]["pattern"] == f"^{regel.pattern}$", name


def test_export_rolle_und_freigeber_abgelehnt(dienst: AktenInterlock) -> None:
    with pytest.raises(ValueError, match="Rolle"):
        mcp_server.baue_server(dienst, rolle="export")
    mit_freigeber = AktenInterlock.erstelle(approver=lambda c: True)
    with pytest.raises(ValueError, match="Freigeber"):
        mcp_server.baue_server(mit_freigeber)


# ------------------------------------------------------------ Ablage


async def test_passendes_dokument_wird_abgelegt(dienst: AktenInterlock) -> None:
    r = await rufe(dienst, "dokument_ablegen", dok_args(**PASSEND))
    assert not r.is_error
    assert r.structured_content["status"] == "abgelegt"
    assert r.structured_content["identitaet"] == "match"
    assert len(dienst.akten[AKTE].dokumente) == 1


async def test_fremddokument_geht_in_quarantaene(dienst: AktenInterlock) -> None:
    args = dok_args(**FREMD)
    r = await rufe(dienst, "dokument_ablegen", args)
    assert r.structured_content["status"] == "quarantaene"
    assert r.structured_content["identitaet"] == "abweichend"
    assert dienst.akten[AKTE].dokumente == []

    q = await rufe(dienst, "quarantaene_auflisten", {"akte_id": AKTE})
    assert [e["dok_id"] for e in q.structured_content["eintraege"]] == [args["dok_id"]]

    v = await rufe(dienst, "akte_export_vorpruefen", {"akte_id": AKTE})
    assert v.structured_content["versandbereit"] is False
    assert v.structured_content["quarantaene_offen"] == 1


async def test_unklar_wird_nie_vom_modell_freigegeben(dienst: AktenInterlock) -> None:
    """Nur die VSNR: Policy verlangt Freigabe. Der Server hat keinen Freigeber."""
    r = await rufe(dienst, "dokument_ablegen", dok_args(vsnr=VSNR_A))
    assert r.structured_content["status"] == "quarantaene"
    assert r.structured_content["identitaet"] == "unklar"
    assert dienst.akten[AKTE].dokumente == []
    holds = [x for x in dienst.audit.records if x.get("verdict") == "hold"]
    assert holds, "HOLD muss im Audit stehen"


async def test_doppeltes_dokument_abgewiesen(dienst: AktenInterlock) -> None:
    args = dok_args(**PASSEND)
    assert (await rufe(dienst, "dokument_ablegen", args)).structured_content["status"] == (
        "abgelegt"
    )
    r = await rufe(dienst, "dokument_ablegen", args)
    assert r.is_error and "bereits" in r.content[0].text
    assert len(dienst.akten[AKTE].dokumente) == 1


@pytest.mark.parametrize(
    ("feld", "wert"),
    [("sha256", "nichthex"), ("akte_id", "ak-klein"), ("eingang", "03.03.2025"), ("seiten", 0)],
)
async def test_formatfehler_vor_dem_pruefkern(dienst: AktenInterlock, feld: str, wert: Any) -> None:
    """Formfehler weist das Schema ab. Sie zählen nicht als Verstoß gegen die Policy."""
    vorher = len(dienst.audit.records)
    r = await rufe(dienst, "dokument_ablegen", dok_args(**PASSEND) | {feld: wert})
    assert r.is_error
    assert not [x for x in dienst.audit.records[vorher:] if x["kind"] == "decision"]


async def test_ungueltiges_kalenderdatum(dienst: AktenInterlock) -> None:
    r = await rufe(dienst, "dokument_ablegen", dok_args(**PASSEND) | {"eingang": "2025-02-30"})
    assert r.is_error and "Datum" in r.content[0].text


async def test_unbekannte_akte(dienst: AktenInterlock) -> None:
    r = await rufe(dienst, "dokument_ablegen", dok_args(**PASSEND) | {"akte_id": "AK-9999"})
    assert r.is_error and "unbekannt" in r.content[0].text


async def test_gesperrte_rolle_kommt_nicht_an_die_akte(dienst: AktenInterlock) -> None:
    dienst.interlock.lockout("posteingang", "Teamleitung", "Test")
    r = await rufe(dienst, "akte_export_vorpruefen", {"akte_id": AKTE})
    assert r.is_error and "gesperrt" in r.content[0].text
    r = await rufe(dienst, "dokument_ablegen", dok_args(**PASSEND))
    assert r.is_error
    assert dienst.akten[AKTE].dokumente == []


async def test_parallele_ablage_verliert_nichts(dienst: AktenInterlock) -> None:
    server = mcp_server.baue_server(dienst)
    ergebnisse: list[str] = []

    async with Client(server) as c:

        async def eins() -> None:
            r = await c.call_tool("dokument_ablegen", dok_args(**PASSEND))
            ergebnisse.append(r.structured_content["status"])

        async with anyio.create_task_group() as tg:
            for _ in range(25):
                tg.start_soon(eins)
    assert ergebnisse == ["abgelegt"] * 25
    assert len(dienst.akten[AKTE].dokumente) == 25


# ------------------------------------------------------------ Datensparsamkeit


async def test_keine_personendaten_in_antworten_und_audit(dienst: AktenInterlock) -> None:
    antworten = []
    async with Client(mcp_server.baue_server(dienst)) as c:
        antworten.append(await c.call_tool("dokument_ablegen", dok_args(**PASSEND)))
        antworten.append(await c.call_tool("dokument_ablegen", dok_args(**FREMD)))
        antworten.append(await c.call_tool("dokument_ablegen", dok_args(vsnr=VSNR_A)))
        antworten.append(await c.call_tool("quarantaene_auflisten", {"akte_id": AKTE}))
        antworten.append(await c.call_tool("akte_export_vorpruefen", {"akte_id": AKTE}))
        antworten.append(await c.call_tool("vsnr_pruefen", {"nummer": VSNR_A}))
    text = json.dumps([a.structured_content for a in antworten] + dienst.audit.records, default=str)
    for wert in PERSONENDATEN:
        assert wert not in text, wert


# ------------------------------------------------------------ Rechenwerkzeuge


async def test_vsnr_pruefen_wird_nicht_protokolliert(dienst: AktenInterlock) -> None:
    vorher = len(dienst.audit.records)
    assert (await rufe(dienst, "vsnr_pruefen", {"nummer": VSNR_A})).structured_content == {
        "gueltig": True,
        "fehler": None,
    }
    r = await rufe(dienst, "vsnr_pruefen", {"nummer": "65170839J004"})
    assert r.structured_content["gueltig"] is False
    # mcp_start je Serveraufbau, sonst nichts
    neu = [x["kind"] for x in dienst.audit.records[vorher:]]
    assert set(neu) == {"mcp_start"}


async def test_fristen_wie_cli(dienst: AktenInterlock) -> None:
    r = await rufe(dienst, "fristen_berechnen", {"eingang": "2025-03-03", "heute": "2025-06-10"})
    ampeln = [f["ampel"] for f in r.structured_content["fristen"]]
    assert ampeln.count("rot") == 3
    assert "Genehmigungsfiktion" in json.dumps(r.structured_content, ensure_ascii=False)


# ------------------------------------------------------------ Laden und CLI


def test_lade_akten_beispiel() -> None:
    pfad = Path(__file__).resolve().parents[1] / "beispiel" / "akten.json"
    akten = mcp_server.lade_akten(pfad)
    assert [a.akte_id for a in akten] == ["AK-2025-0001", "AK-2025-0002"]


def test_lade_akten_fehler_ohne_inhalt(tmp_path: Path) -> None:
    f = tmp_path / "akten.json"
    f.write_text(json.dumps({"akten": [{"akte_id": "AK-1", "vsnr": "Erika Muster kaputt"}]}))
    with pytest.raises(ValueError) as e:
        mcp_server.lade_akten(f)
    assert "Erika" not in str(e.value) and "Nr. 1" in str(e.value)
    f.write_text(json.dumps({"akten": []}))
    with pytest.raises(ValueError, match="keine Akten"):
        mcp_server.lade_akten(f)


def test_cli_verweigert_http_im_netz_ohne_auth(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["mcp", "--akten", "beispiel/akten.json", "--transport", "streamable-http"]
    assert main([*args, "--host", "0.0.0.0"]) == 2
    assert "Abgelehnt" in capsys.readouterr().out


def test_cli_akten_fehlen(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["mcp", "--akten", str(tmp_path / "fehlt.json")]) == 1
    assert "nicht geladen" in capsys.readouterr().out
