"""Kommandozeile: demo | fristen | vsnr | normalisiere | audit-pruefen | mcp."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from agent_interlock import verify

from . import demo, normalisierung, vsnr
from .fristen import Antrag, berechne


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="akten-interlock")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("demo", help="Ausgangsfall mit synthetischen Daten durchspielen")
    p.add_argument("--audit", type=Path, help="Audit-Protokoll als JSONL-Datei schreiben")

    p = sub.add_parser("fristen", help="Fristen nach §§ 14, 18 SGB IX berechnen")
    p.add_argument("--eingang", required=True)
    p.add_argument("--heute", default=date.today().isoformat())
    p.add_argument("--traeger", default="rentenversicherung")
    p.add_argument("--gutachten", action="store_true", help="Gutachten beauftragt")
    p.add_argument("--gutachten-am")
    p.add_argument("--mitteilung-am")
    p.add_argument("--angekuendigt")
    p.add_argument("--entschieden-am")

    p = sub.add_parser("vsnr", help="VSNR auf Format und Prüfziffer prüfen")
    p.add_argument("nummer")

    p = sub.add_parser("normalisiere", help="Datei nach PDF/A, sonst Original beifügen")
    p.add_argument("datei", type=Path)
    p.add_argument("--ziel", type=Path, default=Path("ausgang"))
    p.add_argument("--ohne-foto", action="store_true", help="Handyfoto nicht aufbereiten")

    p = sub.add_parser("audit-pruefen", help="Hash-Kette eines Audit-Protokolls prüfen")
    p.add_argument("datei", type=Path)

    p = sub.add_parser("mcp", help="Als MCP-Werkzeugserver starten (z. B. für F13)")
    p.add_argument("--akten", type=Path, required=True, help="JSON-Datei mit Akten")
    p.add_argument("--rolle", choices=["posteingang", "sachbearbeitung"], default="posteingang")
    p.add_argument("--audit", type=Path, help="Audit-Protokoll (JSONL, hash-verkettet)")
    p.add_argument("--state", type=Path, help="Sperrzustand persistieren (Lockout, Not-Halt)")
    p.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument(
        "--ohne-auth-im-netz",
        action="store_true",
        help="Erlaubt HTTP auf einer Nicht-Loopback-Adresse ohne Authentifizierung (nur Test)",
    )

    a = ap.parse_args(argv)
    if a.cmd == "mcp":
        return _mcp(a)
    if a.cmd == "demo":
        return demo.lauf(a.audit)
    if a.cmd == "fristen":
        antrag = Antrag(
            eingang=date.fromisoformat(a.eingang),
            traeger=a.traeger,
            gutachten_beauftragt=a.gutachten,
            gutachten_liegt_vor=_d(a.gutachten_am),
            begruendete_mitteilung=_d(a.mitteilung_am),
            angekuendigte_entscheidung=_d(a.angekuendigt),
            entschieden=_d(a.entschieden_am),
        )
        for f in berechne(antrag, date.fromisoformat(a.heute)):
            print(f"{f.ampel.value:<9} {f.ende:%d.%m.%Y}  {f.norm:<22} {f.was}  {f.hinweis}")
        return 0
    if a.cmd == "vsnr":
        try:
            v = vsnr.parse(a.nummer)
        except vsnr.VsnrFehler as e:
            print(f"ungültig: {e}")
            return 1
        print(f"gültig: {v.text}")
        return 0
    if a.cmd == "normalisiere":
        if not a.datei.is_file():
            print(f"Datei nicht gefunden: {a.datei}")
            return 1
        r = normalisierung.normalisiere(a.datei, a.ziel, foto_aufbereitung=not a.ohne_foto)
        print(f"{r.darstellung}: {r.ziel} ({r.seiten} S.) sha256={r.sha256_original}")
        print(f"Protokoll: {r.protokoll}")
        print(f"Original: {r.original}")
        if r.maengel:
            print("Rückfrage an Absender empfohlen: " + "; ".join(r.maengel))
        return 0
    if a.cmd == "audit-pruefen":
        recs = [json.loads(z) for z in a.datei.read_text().splitlines() if z.strip()]
        ok, idx = verify(recs)
        print("intakt" if ok else f"GEBROCHEN ab Eintrag {idx}")
        return 0 if ok else 2
    return 1


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _mcp(a: argparse.Namespace) -> int:
    try:
        from agent_interlock import AuditLog

        from .mcp_server import baue_server, erstelle_dienst, lade_akten
    except ImportError:
        print("MCP-Server braucht das Extra: pip install 'akten-interlock[mcp]'")
        return 1
    if a.transport == "streamable-http" and a.host not in _LOOPBACK and not a.ohne_auth_im_netz:
        # Der Server prüft noch keine Tokens. Im Netz nur hinter einem Gateway mit Auth betreiben.
        print(
            f"Abgelehnt: {a.host} ist keine Loopback-Adresse und der Server prüft keine Tokens. "
            "Hinter ein Gateway mit Authentifizierung stellen oder --ohne-auth-im-netz setzen."
        )
        return 2
    try:
        akten = lade_akten(a.akten)
    except (OSError, ValueError) as e:
        print(f"Akten nicht geladen: {e}")
        return 1
    dienst = erstelle_dienst(akten, audit=AuditLog(a.audit), state_pfad=a.state)
    server = baue_server(dienst, a.rolle)
    if a.transport == "stdio":
        server.run()
    else:
        server.run("streamable-http", host=a.host, port=a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
