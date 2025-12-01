# cinemas.py
import math
import requests

# Plusieurs instances Overpass pour éviter les 504
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
]


def _haversine_km(lat1, lon1, lat2, lon2):
    """Distance en km entre deux points GPS."""
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _build_address(tags):
    """Construit une adresse lisible à partir des tags OSM."""
    if not tags:
        return None

    parts = []

    street = tags.get("addr:street")
    number = tags.get("addr:housenumber")
    postcode = tags.get("addr:postcode")
    city = tags.get("addr:city")

    if number and street:
        parts.append(f"{number} {street}")
    elif street:
        parts.append(street)

    if postcode and city:
        parts.append(f"{postcode} {city}")
    elif city:
        parts.append(city)

    return ", ".join(parts) if parts else None


def _call_overpass(query, timeout=25):
    """
    Essaie plusieurs serveurs Overpass.
    Retourne le JSON parsé ou None si tout échoue.
    Ne lève pas d'exception.
    """
    last_exc = None

    for url in OVERPASS_URLS:
        try:
            print(f"🎬 Overpass: appel {url}")
            resp = requests.post(url, data={"data": query}, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"⚠️ Overpass error on {url}: {repr(e)}")
            last_exc = e
            continue

    print("❌ All Overpass endpoints failed:", repr(last_exc))
    return None


def find_cinemas(center_lat, center_lon, radius_km=10.0, max_results=200):
    """
    Recherche des cinémas autour d'un point (via OpenStreetMap / Overpass).

    :param center_lat: latitude du centre
    :param center_lon: longitude du centre
    :param radius_km: rayon en kilomètres
    :param max_results: nombre max de résultats
    :return: liste de cinémas sous forme de dicts
    """
    # Bornes raisonnables
    if radius_km <= 0:
        radius_km = 1.0
    if radius_km > 100:
        radius_km = 100.0

    radius_m = int(radius_km * 1000)

    query = f"""
    [out:json][timeout:25];
    (
      node["amenity"="cinema"](around:{radius_m},{center_lat},{center_lon});
      way["amenity"="cinema"](around:{radius_m},{center_lat},{center_lon});
      relation["amenity"="cinema"](around:{radius_m},{center_lat},{center_lon});
    );
    out center {max_results};
    """

    data = _call_overpass(query, timeout=30)
    if not data:
        # On ne lève rien, le backend renverra simplement 0 cinéma
        return []

    elements = data.get("elements", [])
    cinemas = []

    for el in elements:
        tags = el.get("tags", {}) or {}

        # Coordonnées : nodes => lat/lon, ways/relations => center.lat/lon
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None or lon is None:
            center = el.get("center") or {}
            lat = center.get("lat")
            lon = center.get("lon")

        if lat is None or lon is None:
            continue

        try:
            lat = float(lat)
            lon = float(lon)
        except ValueError:
            continue

        distance_km = round(_haversine_km(center_lat, center_lon, lat, lon), 1)

        name = tags.get("name") or "Cinéma"
        city = tags.get("addr:city")
        address = _build_address(tags)
        website = tags.get("website") or tags.get("contact:website")

        cinemas.append({
            "name": name,
            "latitude": lat,
            "longitude": lon,
            "city": city,
            "address": address,
            "distanceKm": distance_km,
            "website": website,
            "source": "osm",
            "osmType": el.get("type"),
            "osmId": el.get("id"),
        })

    cinemas.sort(key=lambda c: c.get("distanceKm", 9999))
    return cinemas[:max_results]
