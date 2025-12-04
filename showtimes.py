import unicodedata
from datetime import date, datetime

import requests
from allocineAPI.allocineAPI import allocineAPI

# -------------------------------------------------
# Client Allociné et caches
# -------------------------------------------------

_api = allocineAPI()

# { normalized_name: id_depart }
_DEPARTMENTS_BY_NAME = None

# { dept_id_or_city_id: [cinema dict, ...] }
_CINEMAS_BY_DEPT = {}

# { (lat_rounded, lon_rounded): dept_name_or_None }
_DEPT_NAME_CACHE = {}

# { normalized_city_name: id_ville }
_CITIES_BY_NAME = None

# Mapping code postal -> nom de département (Île-de-France, extensible)
DEPT_CODE_TO_NAME = {
    "75": "Paris",
    "77": "Seine-et-Marne",
    "78": "Yvelines",
    "91": "Essonne",
    "92": "Hauts-de-Seine",
    "93": "Seine-Saint-Denis",
    "94": "Val-de-Marne",
    "95": "Val-d'Oise",
}


# -------------------------------------------------
# Utils texte / date
# -------------------------------------------------

def _normalize_text(s):
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


def _get_default_date_str():
    return date.today().strftime("%Y-%m-%d")


# -------------------------------------------------
# Détection du département via Nominatim
# -------------------------------------------------

def _clean_dept_name(name):
    if not name:
        return None
    prefixes = (
        "Département de ",
        "Departement de ",
        "Department of ",
        "Département ",
        "Department ",
    )
    for p in prefixes:
        if name.startswith(p):
            name = name[len(p):]
    return name


def _extract_department_name_from_address(address):
    """
    Essaie de déduire un *département* français à partir de l'adresse Nominatim.
    - priorité à county / state_district
    - cas particulier 'Île-de-France' via le code postal
    - dernier recours : code postal -> département
    """
    if not address:
        return None

    # 1. priorité : county / state_district
    for key in ("county", "state_district"):
        raw = address.get(key)
        if raw:
            return _clean_dept_name(raw)

    # 2. fallback : state (souvent la région)
    state = address.get("state")
    if state == "Île-de-France":
        # On dérive le département via le code postal
        postcode = (address.get("postcode") or "").strip()
        if len(postcode) >= 2:
            code2 = postcode[:2]
            dept = DEPT_CODE_TO_NAME.get(code2)
            if dept:
                return dept
        # On ne renvoie pas la région, car Allociné veut un département
        return None

    if state:
        return _clean_dept_name(state)

    # 3. dernier recours : code postal
    postcode = (address.get("postcode") or "").strip()
    if len(postcode) >= 2:
        code2 = postcode[:2]
        dept = DEPT_CODE_TO_NAME.get(code2)
        if dept:
            return dept

    return None


def _call_nominatim(lat, lon, zoom):
    """Appel générique Nominatim, retourne le dict address ou {}."""
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "lat": lat,
        "lon": lon,
        "format": "json",
        "zoom": zoom,
        "addressdetails": 1,
    }
    headers = {
        "User-Agent": "gedeon-cinemas-showtimes/1.0"
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data.get("address", {}) or {}
        return {}
    except requests.RequestException as e:
        print(f"❌ Erreur Nominatim (reverse zoom={zoom}) pour ({lat}, {lon}): {e}")
        return {}


def _reverse_geocode_department(lat, lon):
    """Retourne le nom du département via Nominatim pour un point GPS."""
    key = (round(lat, 3), round(lon, 3))
    if key in _DEPT_NAME_CACHE:
        return _DEPT_NAME_CACHE[key]

    # 1er essai : zoom moyen (département/région)
    address = _call_nominatim(lat, lon, zoom=10)
    dept_name = _extract_department_name_from_address(address)

    state = address.get("state")
    county = address.get("county")
    postcode = address.get("postcode")

    # Cas particulier : Île-de-France sans county/postcode -> on retente zoom 18
    if not dept_name and state == "Île-de-France" and not county and not postcode:
        print(f"ℹ️ Requête Nominatim plus précise pour point en Île-de-France sans code postal ({lat}, {lon})")
        address2 = _call_nominatim(lat, lon, zoom=18)
        dept_name = _extract_department_name_from_address(address2)
        if dept_name:
            _DEPT_NAME_CACHE[key] = dept_name
            print(f"🗺️ Département détecté (2e passe): {dept_name}")
            return dept_name
        print(
            "⚠️ Impossible de déterminer le département (2e passe) pour "
            f"({lat}, {lon}) via Nominatim "
            f"(state={address2.get('state')!r}, county={address2.get('county')!r}, "
            f"postcode={address2.get('postcode')!r})"
        )

    if dept_name:
        _DEPT_NAME_CACHE[key] = dept_name
        print(f"🗺️ Département détecté: {dept_name}")
        return dept_name

    # Dernier fallback : approximation pour Paris intra-muros
    if 48.80 <= lat <= 48.90 and 2.25 <= lon <= 2.42:
        dept_name = "Paris"
        _DEPT_NAME_CACHE[key] = dept_name
        print(f"🗺️ Département approximé via bounding box: {dept_name}")
        return dept_name

    print(
        "⚠️ Impossible de déterminer le département pour "
        f"({lat}, {lon}) via Nominatim "
        f"(state={state!r}, county={county!r}, postcode={postcode!r})"
    )
    _DEPT_NAME_CACHE[key] = None
    return None


# -------------------------------------------------
# Récupération des départements / villes / cinémas Allociné
# -------------------------------------------------

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


def _load_cities():
    global _CITIES_BY_NAME
    if _CITIES_BY_NAME is not None:
        return

    try:
        ret = _api.get_top_villes() or []
    except Exception as e:
        print(f"❌ Erreur Allociné get_top_villes: {e}")
        _CITIES_BY_NAME = {}
        return

    mapping = {}
    for d in ret:
        name = d.get("name")
        vid = d.get("id")
        if not name or not vid:
            continue
        norm = _normalize_text(name)
        mapping[norm] = vid

    _CITIES_BY_NAME = mapping
    print(f"🏙️ {len(mapping)} villes Allociné chargées")


def _get_city_id_for_name(name):
    """Fallback : si on ne trouve pas de département pour 'Paris', on essaye via les villes."""
    if not name:
        return None

    _load_cities()
    if not _CITIES_BY_NAME:
        return None

    norm = _normalize_text(name)

    # 1) match exact
    if norm in _CITIES_BY_NAME:
        return _CITIES_BY_NAME[norm]

    # 2) tolérance : inclusion
    for k, vid in _CITIES_BY_NAME.items():
        if norm in k or k in norm:
            return vid

    return None


def _get_department_id_for_name(name):
    """Retourne un id Allociné d'emplacement (département ou ville)."""
    if not name:
        return None

    # 1) on tente via les départements
    _load_departments()
    if _DEPARTMENTS_BY_NAME:
        norm = _normalize_text(name)

        # match exact
        if norm in _DEPARTMENTS_BY_NAME:
            return _DEPARTMENTS_BY_NAME[norm]

        # tolérance : inclusion
        for k, did in _DEPARTMENTS_BY_NAME.items():
            if norm in k or k in norm:
                return did

    # 2) fallback : on tente via les villes
    city_id = _get_city_id_for_name(name)
    if city_id:
        print(f"ℹ️ Utilisation de l'id de ville Allociné '{city_id}' pour '{name}' (fallback)")
        return city_id

    return None


def _get_cinemas_for_dept(dept_id):
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


def _find_best_allocine_cinema(dept_id, target_name):
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

        # match exact
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

    # Seuil minimal pour éviter les faux positifs
    if best is not None and best_score >= 2:
        return best
    return None


# -------------------------------------------------
# Formatage des horaires
# -------------------------------------------------

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


# -------------------------------------------------
# API publique utilisée par cinemas.py / server.py
# -------------------------------------------------

def get_showtimes_for_cinema(cinema_name, cinema_lat, cinema_lon, date_str=None):
    """
    Retourne les séances du jour pour un cinéma (via Allociné).

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
        print(f"⚠️ Impossible de trouver l'id de département/ville Allociné pour '{dept_name}'")
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

        formatted.append(
            {
                "title": title,
                "duration": duration,
                "vf": vf_list,
                "vo": vo_list,
                "vost": vost_list,
            }
        )

    return formatted


def enrich_cinemas_with_showtimes(cinemas, date_str=None, max_cinemas=8):
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
