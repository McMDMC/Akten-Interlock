"""akten-interlock: Poka-Yoke für die E-Akte, auf Basis von agent-interlock."""

from .dienst import AktenInterlock, Status, Vorgang
from .fristen import Ampel, Antrag, Frist, berechne
from .identitaet import Ergebnis, Pruefung, pruefe
from .modelle import Akte, Dokument, Merkmale

__all__ = [
    "Akte",
    "AktenInterlock",
    "Ampel",
    "Antrag",
    "Dokument",
    "Ergebnis",
    "Frist",
    "Merkmale",
    "Pruefung",
    "Status",
    "Vorgang",
    "berechne",
    "pruefe",
]
__version__ = "0.2.0"
