import math
import requests

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Rayon max de sécurité
MAX_RADIUS_KM = 100.0


def _haversine_km(lat1, lon1, lat2, lon2):
    """Distance en km entre deux points (latitude/longitude)."""
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _build_overpass_query(lat, lon, radius_km):
    radius_m = int(radius_km * 1000)
    # Cinémas : amenity=cinema ou building=cinema
    return f"""
    [out:json][timeout:25];
    (
      node["amenity"="cinema"](around:{radius_m},{lat},{lon});
      way["amenity"="cinema"](around:{radius_m},{lat},{lon});
      relation["amenity"="cinema"](around:{radius_m},{lat},{lon});
      node["building"="cinema"](around:{radius_m},{lat},{lon});
      way["building"="cinema"](around:{radius_m},{lat},{lon});
      relation["building"="cinema"](around:{radius_m},{lat},{lon});
    );
    out center;
    """


def _query_overpass(lat, lon, radius_km):
    query = _build_overpass_query(lat, lon, radius_km)

    last_error = None
    for url in OVERPASS_URLS:
        try:
            print(f"🎬 Overpass: POST {url} (rayon={radius_km}km)")
            resp = requests.post(
                url,
                data={"data": query},
                timeout=30,
                headers={"User-Agent": "gedeon-cinemas/1.0 (eric@ericmahe.com)"},
            )
            if resp.status_code == 504:
                # Gateway timeout, on tente un autre endpoint
                print(f"⚠️ Overpass 504 sur {url}, on essaie le suivant…")
                last_error = f"504 @ {url}"
                continue

            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"❌ Erreur Overpass sur {url}: {e}")
            last_error = str(e)
            continue

    print(f"❌ Tous les endpoints Overpass ont échoué: {last_error}")
    return {"elements": []}


def find_cinemas(center_lat, center_lon, radius_km=30.0, max_results=50):
    """
    Retourne les cinémas OSM dans un rayon donné autour d'un point GPS.

    Chaque cinéma :
    {
        "id": osm_id,
        "name": "...",
        "address": "...",
        "city": "...",
        "latitude": ...,
        "longitude": ...,
        "distanceKm": ...,
        "osmTags": {...}
    }
    """
    try:
        center_lat = float(center_lat)
        center_lon = float(center_lon)
    except (TypeError, ValueError):
        raise ValueError("Coordonnées invalides pour find_cinemas()")

    if radius_km <= 0:
        radius_km = 1.0
    if radius_km > MAX_RADIUS_KM:
        radius_km = MAX_RADIUS_KM

    data = _query_overpass(center_lat, center_lon, radius_km)
    elements = data.get("elements", [])

    cinemas = []
    seen_ids = set()

    for el in elements:
        osm_id = el.get("id")
        if osm_id in seen_ids:
            continue

        tags = el.get("tags", {}) or {}
        name = tags.get("name")
        if not name:
            # Pas de nom => peu exploitable pour l'utilisateur
            continue

        if el.get("type") == "node":
            lat = el.get("lat")
            lon = el.get("lon")
        else:
            center = el.get("center") or {}
            lat = center.get("lat")
            lon = center.get("lon")

        if lat is None or lon is None:
            continue

        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            continue

        dist = _haversine_km(center_lat, center_lon, lat, lon)
        if dist > radius_km:
            continue

        city = tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village")
        street = tags.get("addr:street")
        housenumber = tags.get("addr:housenumber")

        parts = []
        if housenumber:
            parts.append(str(housenumber))
        if street:
            parts.append(str(street))
        address = " ".join(parts).strip()
        if city:
            address = (address + ", " if address else "") + city

        cinemas.append({
            "id": osm_id,
            "name": name,
            "address": address or None,
            "city": city,
            "latitude": lat,
            "longitude": lon,
            "distanceKm": round(dist, 1),
            "osmTags": tags,
        })
        seen_ids.add(osm_id)

    # Tri par distance
    cinemas.sort(key=lambda c: c["distanceKm"])

    if max_results and max_results > 0:
        cinemas = cinemas[:max_results]

    print(f"🎬 {len(cinemas)} cinémas trouvés dans un rayon de {radius_km} km")
    return cinemas
