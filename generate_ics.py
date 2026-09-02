#!/usr/bin/env python3
r"""
Génère un fichier .ics pour un groupe donné de L3 Info (S5), à partir du
JSON public utilisé par la page "Agenda en ligne" du FIL (Univ. Lille).

Reproduit exactement la logique de filtrage du script client
(calendarL3S56.js) :
  - un événement dont le titre matche "^(..) (\w+)/" avec un code parmi
    CM / TD / TP est un cours normal, dont l'UE est le 2e groupe capturé.
  - sinon l'événement est "spécial" (jours fériés, réunions, etc.) et est
    TOUJOURS inclus, quel que soit le groupe demandé.
  - le groupe d'un cours est extrait via "(G\d)" dans le titre ; si absent,
    le cours est considéré comme commun à tous (G1 par défaut côté JS,
    mais on le traite ici comme "commun" -> toujours inclus).
  - un cours CM est toujours inclus, quel que soit le groupe demandé.
  - un cours TD/TP n'est inclus que si son groupe correspond au groupe demandé.

Usage:
    python3 generate_ics.py G4 output.ics
    python3 generate_ics.py All output.ics   # tout le monde, sans filtrage
"""

import sys
import re
import json
import uuid
from datetime import datetime, timezone
from urllib.request import urlopen, Request

JSON_URL_TEMPLATE = "https://www.fil.univ-lille.fr/~aubert/l3/agenda/{prefix}.json"
YEAR_PREFIX = "2627-S5-All"  # correspond à l'année scolaire en cours sur le portail

COURSE_RE = re.compile(r"^(..) (\w+)/")
GROUP_RE = re.compile(r"\(G(\d)\)")

NATURE_WHITELIST = {"CM", "TD", "TP"}


def fetch_events(url: str) -> list:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (ics-sync-script)"})
    with urlopen(req, timeout=20) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def normalize_event(evt: dict) -> dict:
    """Ajoute short/nature/group/is_special à evt, comme normalizeEvent() en JS."""
    title = evt.get("title", "")
    course_match = COURSE_RE.match(title)
    group_match = GROUP_RE.search(title)

    if course_match is None or course_match.group(1) not in NATURE_WHITELIST:
        evt["short"] = "special"
        evt["nature"] = "special"
        evt["group"] = None
        evt["is_special"] = True
    else:
        evt["short"] = course_match.group(2)
        evt["nature"] = course_match.group(1)
        evt["group"] = group_match.group(1) if group_match else None
        evt["is_special"] = False

    return evt


def keep_event(evt: dict, target_group: str) -> bool:
    """Réplique exactement selectEvent() du JS."""
    if target_group == "All":
        return True
    if evt["is_special"]:
        return True
    if evt["nature"] == "CM":
        return True
    # group extrait sous forme "4" (sans le "G"), target_group est du style "G4"
    evt_group = f"G{evt['group']}" if evt["group"] else None
    return evt_group == target_group


def parse_iso_utc(s: str) -> datetime:
    # Les dates arrivent au format "2026-09-16T06:30:00.000Z"
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def fold_line(line: str) -> str:
    """RFC5545: replie les lignes de plus de 75 octets."""
    if len(line.encode("utf-8")) <= 75:
        return line
    out = []
    while len(line.encode("utf-8")) > 75:
        # coupe à 74 caractères (approximation suffisante pour de l'ASCII/latin)
        out.append(line[:74])
        line = " " + line[74:]
    out.append(line)
    return "\r\n".join(out)


def escape_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .strip()
    )


def build_ics(events: list, calendar_name: str) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//L3-Info-FIL//Agenda Sync//FR",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{escape_text(calendar_name)}",
        "X-WR-TIMEZONE:UTC",
    ]

    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for evt in events:
        dtstart = parse_iso_utc(evt["start"]).strftime("%Y%m%dT%H%M%SZ")
        dtend = parse_iso_utc(evt["end"]).strftime("%Y%m%dT%H%M%SZ")
        # UID stable : basé sur le contenu, pour éviter les doublons entre
        # deux régénérations successives du fichier.
        uid_source = f"{evt['start']}-{evt['end']}-{evt.get('title','')}"
        uid = uuid.uuid5(uuid.NAMESPACE_URL, uid_source)

        lines.append("BEGIN:VEVENT")
        lines.append(fold_line(f"UID:{uid}@l3-fil-univ-lille"))
        lines.append(f"DTSTAMP:{now_stamp}")
        lines.append(f"DTSTART:{dtstart}")
        lines.append(f"DTEND:{dtend}")
        lines.append(fold_line(f"SUMMARY:{escape_text(evt.get('title', ''))}"))
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 generate_ics.py <GROUPE: G1..G6|All> <fichier_sortie.ics>")
        sys.exit(1)

    target_group = sys.argv[1]
    output_path = sys.argv[2]

    url = JSON_URL_TEMPLATE.format(prefix=YEAR_PREFIX)
    print(f"Téléchargement de {url} ...")
    raw_events = fetch_events(url)
    print(f"{len(raw_events)} événements récupérés au total.")

    normalized = [normalize_event(e) for e in raw_events]
    filtered = [e for e in normalized if keep_event(e, target_group)]
    print(f"{len(filtered)} événements retenus pour le groupe '{target_group}'.")

    ics_content = build_ics(filtered, f"L3 Info S5 - {target_group}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ics_content)

    print(f"Fichier ICS écrit dans {output_path}")


if __name__ == "__main__":
    main()
