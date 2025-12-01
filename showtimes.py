import unicodedata
from datetime import date, datetime

import requests
from allocineAPI.allocineAPI import allocineAPI

# Client Allociné
_api = allocineAPI()

# Caches simples
_DEPARTMENTS_BY_NAME = None   # { normalized_name: id }
_CINEMAS_BY_DEPT = {}         # { dept_id: [cinema dict ...] }
_DEPT_NAME_CACHE = {}         # { (lat_rounded, lon_rounded): dept_name }


def _normalize_text(s: str) -> str:
    """Normalisation simple : minuscules, suppression des accents / ponctuation."""
    if not s:
        return ""
    s = s.strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789 "
    s = "".join(ch if ch in allowed else " " for ch in s)
    s = " ".join(s.split())
    return s


def _get_default_date_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def _reverse_geocode_department(lat: float, lon: float) -> str | None:
    """Retourne le nom du département via Nominatim pour un point GPS."""
    key = (round(lat, 3), round(lon, 3))
    if key in _DEPT_NAME_CACHE:
        return _DEPT_NAME_CACHE[key]

    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "lat": lat,
        "lon": lon,
        "format": "json",
        "zoom": 10,
        "addressdetails": 1,
    }
    headers = {
        "User-Agent": "gedeon-cinemas-showtimes/1.0 (eric@ericmahe.com)"
    }

    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        address = data.get("address", {}) if isinstance(data, dict) else {}

        dept_name = (
            address.get("county")
            or address.get("state_district")
            or address.get("state")
        )
        if dept_name:
            _DEPT_NAME_CACHE[key] = dept_name
            print(f"🗺️ Département détecté: {dept_name}")
            return dept_name

    except requests.RequestException as e:
        print(f"❌ Erreur Nominatim (reverse) pour ({lat}, {lon}): {e}")

    _DEPT_NAME_CACHE[key] = None
    return None


def _load_departments():
    global _DEPARTMENTS_BY_NAME
    if _DEPARTMENTS_BY_NAME is not None:
        return

    try:
        ret = _api.get_departements() or []
    except Exception as e:
        print(f"❌ Erreur Allociné get_departements: {e}")
        _DEPARTMENTS_BY_NAME = {}
        return

    mapping = {}
    for d in ret:
        name = d.get("name")
        did = d.get("id")
        if not name or not did:
            continue
        norm = _normalize_text(name)
        mapping[norm] = did

    _DEPARTMENTS_BY_NAME = mapping
    print(f"📚 {len(mapping)} départements Allociné chargés")


def _get_department_id_for_name(name: str) -> str | None:
    if not name:
        return None
    _load_departments()
    if not _DEPARTMENTS_BY_NAME:
        return None

    norm = _normalize_text(name)
    if norm in _DEPARTMENTS_BY_NAME:
        return _DEPARTMENTS_BY_NAME[norm]

    # Petites tolérances (ex: "departement de herault" vs "herault")
    for k, did in _DEPARTMENTS_BY_NAME.items():
        if norm in k or k in norm:
            return did
    return None


def _get_cinemas_for_dept(dept_id: str):
    if dept_id in _CINEMAS_BY_DEPT:
        return _CINEMAS_BY_DEPT[dept_id]
    try:
        ret = _api.get_cinema(dept_id) or []
        _CINEMAS_BY_DEPT[dept_id] = ret
        print(f"🎬 {len(ret)} cinémas Allociné pour {dept_id}")
        return ret
    except Exception as e:
        print(f"❌ Erreur Allociné get_cinema({dept_id}): {e}")
        _CINEMAS_BY_DEPT[dept_id] = []
        return []


def _find_best_allocine_cinema(dept_id: str, target_name: str) -> dict | None:
    candidates = _get_cinemas_for_dept(dept_id)
    if not candidates:
        return None

    target_norm = _normalize_text(target_name)
    if not target_norm:
        return None

    best = None
    best_score = -1

    for c in candidates:
        cname = c.get("name")
        if not cname:
            continue
        c_norm = _normalize_text(cname)

        if c_norm == target_norm:
            return c

        score = 0
        if target_norm in c_norm or c_norm in target_norm:
            score += 3

        # Overlap de mots
        t_tokens = set(target_norm.split())
        c_tokens = set(c_norm.split())
        if t_tokens and c_tokens:
            overlap = len(t_tokens & c_tokens)
            score += overlap

        if score > best_score:
            best_score = score
            best = c

    # Seuil minimal : si vraiment trop faible, on évite le faux positif
    if best is not None and best_score >= 2:
        return best
    return None


def _format_showtime_list(raw_list):
    """Convertit les horaires ISO en 'HH:MM' lisibles."""
    times = []
    for s in raw_list or []:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            times.append(dt.strftime("%H:%M"))
        except Exception:
            continue
    return times


def get_showtimes_for_cinema(cinema_name: str, cinema_lat: float, cinema_lon: float,
                             date_str: str | None = None):
    """
    Retourne les séances du jour pour un cinéma (via Allociné) :

    [
      {
        "title": "...",
        "duration": "...",
        "vf": ["14:00", "16:30"],
        "vo": [...],
        "vost": [...]
      },
      ...
    ]
    """
    if date_str is None:
        date_str = _get_default_date_str()

    try:
        cinema_lat = float(cinema_lat)
        cinema_lon = float(cinema_lon)
    except (TypeError, ValueError):
        return []

    dept_name = _reverse_geocode_department(cinema_lat, cinema_lon)
    if not dept_name:
        return []

    dept_id = _get_department_id_for_name(dept_name)
    if not dept_id:
        print(f"⚠️ Impossible de trouver l'id de département Allociné pour '{dept_name}'")
        return []

    best_cinema = _find_best_allocine_cinema(dept_id, cinema_name)
    if not best_cinema:
        print(f"⚠️ Aucun cinéma Allociné correspondant pour '{cinema_name}' dans {dept_id}")
        return []

    cinema_id = best_cinema.get("id")
    if not cinema_id:
        return []

    try:
        raw_showtimes = _api.get_showtime(cinema_id, date_str) or []
    except Exception as e:
        print(f"❌ Erreur Allociné get_showtime({cinema_id}, {date_str}): {e}")
        return []

    formatted = []
    for entry in raw_showtimes:
        title = entry.get("title")
        duration = entry.get("duration")
        vf_list = _format_showtime_list(entry.get("VF"))
        vo_list = _format_showtime_list(entry.get("VO"))
        vost_list = _format_showtime_list(entry.get("VOST"))

        formatted.append({
            "title": title,
            "duration": duration,
            "vf": vf_list,
            "vo": vo_list,
            "vost": vost_list,
        })

    return formatted


def enrich_cinemas_with_showtimes(cinemas: list, date_str: str | None = None,
                                  max_cinemas: int = 8):
    """
    Ajoute les séances du jour aux objets cinémas (in-place) :

    cinéma["showtimes"] = [...]
    cinéma["showtimesDate"] = "YYYY-MM-DD"
    """
    if not cinemas:
        return cinemas

    if date_str is None:
        date_str = _get_default_date_str()

    count = 0
    for cinema in cinemas:
        if count >= max_cinemas:
            break

        name = cinema.get("name")
        lat = cinema.get("latitude")
        lon = cinema.get("longitude")
        if not name or lat is None or lon is None:
            continue

        showtimes = get_showtimes_for_cinema(name, lat, lon, date_str=date_str)
        if showtimes:
            cinema["showtimes"] = showtimes
            cinema["showtimesDate"] = date_str
            count += 1

    print(f"🎞️ Séances ajoutées pour {count} cinémas")
    return cinemas
