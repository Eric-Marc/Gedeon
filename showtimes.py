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

# Mapping code postal -> nom de département (principalement Île-de-France, extensible)
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


# -------------------------------------------------------------------
# Utils texte
# -------------------------------------------------------------------

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


# -------------------------------------------------------------------
# Détection du département via Nominatim
# -------------------------------------------------------------------

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
    - On préfère county / state_district
    - On gère le cas particulier 'Île-de-France' via le code postal
    - Dernier recours : code postal -> département
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
        # Cas très fréquent : on dérive le département via le code postal
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

    # Cas particulier : Île-de-France sans county/postcode -> on retente en zoom fin
    state = address.get("state")
    county = address.get("county")
    postcode = address.get("postcode")

    if not dept_name and state == "Île-de-France" and not county and not postcode:
        print(f"ℹ️ Requête Nominatim plus précise pour point en Île-de-France sans code postal ({lat}, {lon})")
        address2 = _call_nominatim(lat, lon, zoom=18)
        dept_name = _extract_department_name_from_address(address2)
        if dept_name:
            _DEPT_NAME_CACHE[key] = dept_name
            print(f"🗺️ Département détecté (2e passe): {dept_name}")
            return dept_name
        # log détaillé
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

    # Dernier fallback : approximation grossière pour Paris intra-muros
    if 48.80 <= lat <= 48.90 and 2.25 <= lon <= 2.42:
        dept_name = "Paris"
        _DEPT_NAME_CACHE[key] = dept_name
        print(f"🗺️ Département approximé via bounding box: {dept_name}")
        return dept_name

    # Échec complet
    print(
        "⚠️ Impossible de déterminer le département pour "
        f"({lat}, {lon}) via Nominatim "
        f"(state={state!r}, county={county!r}, postcode={postcode!r})"
    )
    _DEPT_NAME_CACHE[key] = None
    return None


# -------------------------------------------------------------------
# Récupération des départements / cinémas Allociné
# -------------------------------------------------------------------

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


def _get_department_id_for_name(name):
    if not name:
        return None
    _load_departments()
    if not _DEPARTMENTS_BY_NAME:
        return None

    norm = _normalize_text(name)

    # 1) match exact
    if norm in _DEPARTMENTS_BY_NAME:
        return _DEPARTMENTS_BY_NAME[norm]

    # 2) tolérance : inclusion
    for k, did in _DEPARTMENTS_BY_NAME.items():
        if norm in k or k in norm:
            return did

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
