# showtimes.py
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List

from allocineAPI.allocineAPI import allocineAPI  # fourni par allocine-seances

# Instance globale réutilisée
_api = allocineAPI()


def _norm(s: Optional[str]) -> str:
    """Normalisation simple pour comparer des noms (minuscule + strip)."""
    return s.lower().strip() if s else ""


def _find_location_id_for_city(city: Optional[str]) -> Optional[str]:
    """
    Trouve un id de localisation Allociné (ville-XXXX…) à partir du nom de ville.
    Utilise get_top_villes() de l'API. :contentReference[oaicite:1]{index=1}
    """
    if not city:
        return None

    target = _norm(city)

    try:
        villes = _api.get_top_villes()  # liste de {'id': 'ville-xxx', 'name': 'Montpellier', ...}
    except Exception as e:
        print("AllocineAPI get_top_villes error:", repr(e))
        return None

    # 1) match exact (insensible à la casse)
    for v in villes:
        name = _norm(v.get("name"))
        if name == target:
            return v.get("id")

    # 2) match partiel (Montpellier / Montpellier (Agglo) / etc.)
    for v in villes:
        name = _norm(v.get("name"))
        if target in name or name in target:
            return v.get("id")

    return None


def _find_cinema_id(location_id: str, cinema_name: str, city: Optional[str]) -> Optional[str]:
    """
    Dans une localisation Allociné (ville ou département), trouve l'id du cinéma correspondant
    au nom OSM + ville. Utilise get_cinema(id_location). :contentReference[oaicite:2]{index=2}
    """
    try:
        cinemas = _api.get_cinema(location_id)  # [{'id': 'B0242', 'name': '...', 'address': '...'}, ...]
    except Exception as e:
        print("AllocineAPI get_cinema error:", repr(e))
        return None

    if not cinemas:
        return None

    target = _norm(cinema_name)
    city_norm = _norm(city)

    best = None
    best_score = 0

    for c in cinemas:
        cname = _norm(c.get("name"))
        addr = _norm(c.get("address"))

        score = 0

        # matching sur le nom de cinéma
        if target and cname == target:
            score += 3
        elif target and (target in cname or cname in target):
            score += 2

        # matching sur la ville dans l'adresse
        if city_norm and city_norm in addr:
            score += 1

        if score > best_score:
            best_score = score
            best = c

    if best_score == 0 or best is None:
        return None

    return best.get("id")


def _convert_showtime_item(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convertit un élément renvoyé par api.get_showtime() en une liste de films
    au format générique pour le front :
      {
        "movieTitle": "...",
        "duration": "1h 30min",
        "language": "VF" / "VO",
        "is3D": False,
        "showtimes": ["14:00", "16:30", ...]
      }
    :contentReference[oaicite:3]{index=3}
    """
    out: List[Dict[str, Any]] = []

    title = item.get("title")
    duration = item.get("duration")

    for key, lang_label in (("VF", "VF"), ("VO", "VO")):
        seances = item.get(key) or []
        if not seances:
            continue

        times: List[str] = []
        for dt_str in seances:
            # Ex: "2023-04-15T13:45:00"
            try:
                dt = datetime.fromisoformat(dt_str)
                times.append(dt.strftime("%H:%M"))
            except Exception:
                # si parsing foire, on garde la chaîne brute
                times.append(dt_str)

        out.append({
            "movieTitle": title,
            "duration": duration,
            "language": lang_label,
            "is3D": False,          # on pourrait raffiner en analysant title/duration
            "showtimes": times,
        })

    return out


def get_showtimes(cinema_name: str, city: Optional[str], date_str: str) -> Dict[str, Any]:
    """
    Récupère les séances Allociné pour un cinéma (par nom + ville) et une date YYYY-MM-DD.
    - city sert à trouver l'id "ville-XXXXX"
    - cinema_name sert à matcher dans la liste des cinémas de cette ville.

    Retourne un dict :
      {
        "cinemaName": ...,
        "city": ...,
        "date": "YYYY-MM-DD",
        "showtimes": [ {movieTitle, duration, language, is3D, showtimes[]}, ... ]
      }
    """
    if not date_str:
        tz = ZoneInfo("Europe/Paris")
        date_str = datetime.now(tz).strftime("%Y-%m-%d")

    # 1) localiser la ville Allociné
    loc_id = _find_location_id_for_city(city)
    if not loc_id:
        print(f"[Allocine] Aucun id de localisation trouvé pour la ville '{city}'")
        return {
            "cinemaName": cinema_name,
            "city": city,
            "date": date_str,
            "showtimes": [],
        }

    # 2) trouver l'id du cinéma dans cette localisation
    cinema_id = _find_cinema_id(loc_id, cinema_name, city)
    if not cinema_id:
        print(f"[Allocine] Aucun cinéma correspondant à '{cinema_name}' ({city}) pour loc_id={loc_id}")
        return {
            "cinemaName": cinema_name,
            "city": city,
            "date": date_str,
            "showtimes": [],
        }

    # 3) récupérer les séances
    try:
        raw = _api.get_showtime(cinema_id, date_str)  # liste de dicts
    except Exception as e:
        print("AllocineAPI get_showtime error:", repr(e))
        return {
            "cinemaName": cinema_name,
            "city": city,
            "date": date_str,
            "showtimes": [],
        }

    films: List[Dict[str, Any]] = []
    for item in raw or []:
        films.extend(_convert_showtime_item(item))

    return {
        "cinemaName": cinema_name,
        "city": city,
        "date": date_str,
        "showtimes": films,
    }
