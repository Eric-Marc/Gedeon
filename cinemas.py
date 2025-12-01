# cinemas.py
import math
import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def find_cinemas(lat, lon, radius_m=5000):
    """
    Cherche les cinémas (amenity=cinema) dans un rayon donné autour d'un point GPS.

    :param lat: latitude (float)
    :param lon: longitude (float)
    :param radius_m: rayon en mètres (int)
    :return: liste de dicts avec infos sur chaque cinéma
    """
    query = f"""
    [out:json][timeout:25];
    (
      node["amenity"="cinema"](around:{radius_m},{lat},{lon});
      way["amenity"="cinema"](around:{radius_m},{lat},{lon});
      relation["amenity"="cinema"](around:{radius_m},{lat},{lon});
    );
    out center;
    """

    response = requests.post(OVERPASS_URL, data={"data": query})
    response.raise_for_status()
    data = response.json()

    cinemas = []

    for element in data.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name", "Sans nom")

        if element["type"] == "node":
            cinema_lat = element.get("lat")
            cinema_lon = element.get("lon")
        else:
            center = element.get("center", {})
            cinema_lat = center.get("lat")
            cinema_lon = center.get("lon")

        cinemas.append(
            {
                "id": element.get("id"),
                "osm_type": element.get("type"),
                "name": name,
                "lat": cinema_lat,
                "lon": cinema_lon,
                "tags": tags,
            }
        )

    return cinemas


def haversine_distance_m(lat1, lon1, lat2, lon2):
    """
    Distance (en mètres) entre deux points GPS avec la formule de Haversine.
    """
    R = 6371000  # rayon de la Terre en mètres
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c
