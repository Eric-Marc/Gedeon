from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timezone, timedelta
import json
import os
import math
import requests

# -------------------------------------------------
# Configuration de l'application
# -------------------------------------------------

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

DATA_FILE = 'locations.json'

# === OpenAgenda ===
API_KEY = os.environ.get("OPENAGENDA_API_KEY", "218909f158934e1badf3851a650ad6c1")
BASE_URL = os.environ.get("OPENAGENDA_BASE_URL", "https://api.openagenda.com/v2")

# Valeurs par défaut (France entière)
RADIUS_KM_DEFAULT = 300      # par défaut 300 km
DAYS_AHEAD_DEFAULT = 7       # par défaut 7 jours

# Cache simple en mémoire pour les géocodages Nominatim
GEOCODE_CACHE = {}


# -------------------------------------------------
# Fonctions utilitaires : stockage des positions
# -------------------------------------------------

def load_locations():
    """Charge la liste des positions depuis un fichier JSON local."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_locations(locations):
    """Sauvegarde la liste des positions dans un fichier JSON local."""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(locations, f, ensure_ascii=False, indent=2)


def add_location(latitude, longitude, accuracy=None):
    """Ajoute une position (téléphone) dans l'historique."""
    locations = load_locations()
    entry = {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "accuracy": float(accuracy) if accuracy is not None else None,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    locations.append(entry)
    save_locations(locations)
    return entry


def get_latest_location():
    """Retourne la dernière position enregistrée (ou None)."""
    locations = load_locations()
    if not locations:
        return None
    return locations[-1]


# -------------------------------------------------
# Fonctions utilitaires : calculs géographiques
# -------------------------------------------------

def calculate_bounding_box(lat, lng, radius_km):
    """
    Calculate bounding box coordinates from a center point and radius.

    Args:
        lat: Center latitude
        lng: Center longitude
        radius_km: Radius in kilometers

    Returns:
        Dictionary with northEast and southWest coordinates
    """
    # Earth's radius in kilometers
    EARTH_RADIUS_KM = 6371.0

    # Convert radius to radians
    radius_rad = radius_km / EARTH_RADIUS_KM

    # Convert lat/lng to radians
    lat_rad = math.radians(lat)
    lng_rad = math.radians(lng)

    # Calculate latitude bounds
    lat_delta = math.degrees(radius_rad)
    min_lat = lat - lat_delta
    max_lat = lat + lat_delta

    # Calculate longitude bounds (accounting for latitude)
    lng_delta = math.degrees(radius_rad / math.cos(lat_rad))
    min_lng = lng - lng_delta
    max_lng = lng + lng_delta

    return {
        'northEast': {'lat': max_lat, 'lng': max_lng},
        'southWest': {'lat': min_lat, 'lng': min_lng}
    }


def haversine_km(lat1, lon1, lat2, lon2):
    """Distance en km entre deux points (latitude/longitude)."""
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# -------------------------------------------------
# Fonctions utilitaires : OpenAgenda
# -------------------------------------------------

def search_agendas(search_term=None, official=None, limit=200):
    """
    Recherche d'agendas.
    - Si search_term est None : agendas associés à la clé API
      (France entière pour CETTE clé, pas "tout OpenAgenda").
    """
    url = f"{BASE_URL}/agendas"
    params = {
        "key": API_KEY,  # CORRECTION: La clé doit être dans les paramètres, pas les en-têtes
        "size": min(limit, 300)
    }

    if search_term:
        params["search"] = search_term
    if official is not None:
        params["official"] = 1 if official else 0

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json() or {}
    except requests.exceptions.RequestException as e:
        print(f"❌ Error searching agendas: {e}")
        return {"agendas": []}


def get_events_from_agenda(agenda_uid, center_lat, center_lon, radius_km, days_ahead, limit=300):
    """
    Récupère les événements d'un agenda avec filtrage géographique et temporel via l'API.
    
    CORRECTION MAJEURE: Utilise les paramètres geo[] et timings[] pour filtrer via l'API
    comme dans find_events.py
    """
    url = f"{BASE_URL}/agendas/{agenda_uid}/events"

    # Calculate bounding box
    bbox = calculate_bounding_box(center_lat, center_lon, radius_km)
    
    # Date filtering
    today = datetime.now()
    today_str = today.strftime('%Y-%m-%d')
    end_date = today + timedelta(days=days_ahead)
    end_date_str = end_date.strftime('%Y-%m-%d')

    params = {
        'key': API_KEY,
        'size': min(limit, 300),
        'detailed': 1,
        # CORRECTION: Ajout du filtrage géographique via l'API
        'geo[northEast][lat]': bbox['northEast']['lat'],
        'geo[northEast][lng]': bbox['northEast']['lng'],
        'geo[southWest][lat]': bbox['southWest']['lat'],
        'geo[southWest][lng]': bbox['southWest']['lng'],
        # CORRECTION: Ajout du filtrage temporel via l'API
        'timings[gte]': today_str,
        'timings[lte]': end_date_str,
    }

    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json() or {}
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching events from agenda {agenda_uid}: {e}")
        return {"events": []}


def parse_iso_datetime(s):
    """Parse une date ISO en datetime UTC (offset-aware)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# -------------------------------------------------
# Géocodage Nominatim / OpenStreetMap
# -------------------------------------------------

def geocode_address_nominatim(address_str):
    """
    Géocode une adresse texte avec Nominatim (OpenStreetMap).

    ⚠️ IMPORTANT :
    - respecter les conditions d'utilisation de Nominatim
    - toujours envoyer un User-Agent avec un contact (site ou email)
    """
    if not address_str:
        return None, None

    # Cache en mémoire pour ne pas re-géocoder la même adresse
    if address_str in GEOCODE_CACHE:
        return GEOCODE_CACHE[address_str]

    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": address_str,
        "format": "json",
        "limit": 1
    }
    headers = {
        "User-Agent": "gedeon-demo/1.0 (eric@ericmahe.com)"
    }

    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data:
            GEOCODE_CACHE[address_str] = (None, None)
            return None, None

        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
        GEOCODE_CACHE[address_str] = (lat, lon)
        print(f"🌍 Nominatim geocode OK: '{address_str}' -> ({lat}, {lon})")
        return lat, lon
    except requests.RequestException as e:
        print(f"❌ Nominatim error for '{address_str}': {e}")
        GEOCODE_CACHE[address_str] = (None, None)
        return None, None
    except (KeyError, ValueError) as e:
        print(f"❌ Nominatim parse error for '{address_str}': {e}")
        GEOCODE_CACHE[address_str] = (None, None)
        return None, None


# -------------------------------------------------
# Routes front
# -------------------------------------------------

@app.route('/')
def index():
    """Renvoie la page HTML principale."""
    return send_from_directory('.', 'index.html')


# -------------------------------------------------
# API : positions
# -------------------------------------------------

@app.route('/api/location', methods=['GET', 'POST', 'DELETE'])
def location_collection():
    if request.method == 'GET':
        locations = load_locations()
        return jsonify({
            "status": "success",
            "count": len(locations),
            "locations": locations,
        }), 200

    if request.method == 'POST':
        try:
            data = request.get_json(force=True)
        except Exception:
            return jsonify({"status": "error", "message": "Corps JSON invalide"}), 400

        latitude = data.get("latitude")
        longitude = data.get("longitude")
        accuracy = data.get("accuracy")

        if latitude is None or longitude is None:
            return jsonify({"status": "error", "message": "latitude et longitude sont requises"}), 400

        try:
            entry = add_location(latitude, longitude, accuracy)
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

        return jsonify({"status": "success", "location": entry}), 201

    if request.method == 'DELETE':
        try:
            save_locations([])
            return jsonify({
                "status": "success",
                "message": "Toutes les positions ont été supprimées"
            }), 200
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "error", "message": "Méthode non supportée"}), 405


@app.route('/api/location/latest', methods=['GET'])
def location_latest():
    latest = get_latest_location()
    if latest is None:
        return jsonify({
            "status": "error",
            "message": "Aucune position enregistrée pour le moment"
        }), 404

    return jsonify({
        "status": "success",
        "location": latest,
    }), 200


# -------------------------------------------------
# API : événements à proximité (rayon / jours paramétrables, France entière)
# -------------------------------------------------

@app.route('/api/events/nearby', methods=['GET'])
def events_nearby():
    """
    Cherche des événements autour du dernier point de localisation du téléphone,
    sur l'ensemble des agendas accessibles à la clé API (France entière pour CETTE clé).

    Paramètres de requête (GET) :
      - radiusKm : rayon en kilomètres (float, optionnel)
      - days     : nombre de jours à venir (int, optionnel)
    """
    try:
        # 0. Lecture des paramètres de filtrage
        radius_param = request.args.get("radiusKm", type=float)
        days_param = request.args.get("days", type=int)

        radius_km = radius_param if (radius_param is not None and radius_param > 0) else RADIUS_KM_DEFAULT
        days_ahead = days_param if (days_param is not None and days_param >= 0) else DAYS_AHEAD_DEFAULT

        # Rayon max (sécurité)
        if radius_km > 1000:
            radius_km = 1000.0

        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days_ahead)

        # 1. Dernier point de localisation (téléphone)
        latest = get_latest_location()
        if latest is None:
            return jsonify({
                "status": "error",
                "message": "Aucune position enregistrée, impossible de chercher des événements."
            }), 404

        center_lat = latest.get("latitude")
        center_lon = latest.get("longitude")
        if center_lat is None or center_lon is None:
            return jsonify({
                "status": "error",
                "message": "Dernier point invalide (latitude/longitude manquantes)."
            }), 500

        try:
            center_lat = float(center_lat)
            center_lon = float(center_lon)
        except ValueError:
            return jsonify({
                "status": "error",
                "message": "Coordonnées invalides."
            }), 500

        print(f"🔍 Recherche d'événements autour de ({center_lat}, {center_lon}), rayon={radius_km}km, jours={days_ahead}")

        # 2. Recherche d'agendas (France entière pour cette clé)
        agendas_result = search_agendas(limit=200)
        agendas = agendas_result.get('agendas', []) if agendas_result else []
        total_agendas = len(agendas)

        print(f"📚 {total_agendas} agendas trouvés")

        # stats debug
        agendas_with_events = 0
        total_events_scanned = 0
        total_events_after_geo_filter = 0
        total_events_after_distance = 0
        min_distance = None

        if not agendas:
            return jsonify({
                "status": "success",
                "center": {"latitude": center_lat, "longitude": center_lon},
                "radiusKm": radius_km,
                "days": days_ahead,
                "events": [],
                "count": 0,
                "info": "Aucun agenda trouvé pour cette clé API.",
                "debug": {
                    "totalAgendas": 0,
                    "agendasWithEvents": 0,
                    "totalEventsScanned": 0,
                    "totalEventsAfterGeoFilter": 0,
                    "totalEventsAfterDistanceFilter": 0,
                    "minDistanceKm": None
                }
            }), 200

        # 3. Récupération des événements agenda par agenda avec filtrage API
        all_events = []

        for idx, agenda in enumerate(agendas):
            uid = agenda.get('uid')
            agenda_slug = agenda.get('slug')
            title = agenda.get('title', {})
            if isinstance(title, dict):
                agenda_title = title.get('fr') or title.get('en') or 'Agenda'
            else:
                agenda_title = title or 'Agenda'

            print(f"📖 [{idx+1}/{total_agendas}] Agenda: {agenda_title} ({uid})")

            # CORRECTION MAJEURE: Passer les coordonnées et le rayon à la fonction
            events_data = get_events_from_agenda(uid, center_lat, center_lon, radius_km, days_ahead, limit=300)
            events = events_data.get('events', []) if events_data else []
            
            print(f"   → {len(events)} événements retournés par l'API")
            
            total_events_scanned += len(events)

            if events:
                agendas_with_events += 1

            for ev in events:
                # L'API a déjà filtré par date et bounding box
                total_events_after_geo_filter += 1

                # Récupération du timing
                timings = ev.get('timings') or []
                begin_str = None
                end_str = None
                if timings:
                    first_timing = timings[0]
                    begin_str = first_timing.get('begin')
                    end_str = first_timing.get('end')

                # Récupération de la localisation
                loc = ev.get('location') or {}
                ev_lat = loc.get('latitude')
                ev_lon = loc.get('longitude')

                # Si OpenAgenda ne fournit pas de lat/lon, on tente Nominatim
                if ev_lat is None or ev_lon is None:
                    parts = []
                    if loc.get("name"):
                        parts.append(str(loc["name"]))
                    if loc.get("address"):
                        parts.append(str(loc["address"]))
                    if loc.get("city"):
                        parts.append(str(loc["city"]))
                    parts.append("France")
                    address_str = ", ".join(parts)

                    geocoded_lat, geocoded_lon = geocode_address_nominatim(address_str)
                    if geocoded_lat is not None and geocoded_lon is not None:
                        ev_lat = geocoded_lat
                        ev_lon = geocoded_lon
                    else:
                        # Impossible de géocoder => on ignore pour la carte
                        print(f"   ⚠️  Pas de coordonnées pour: {ev.get('title', 'Sans titre')}")
                        continue

                try:
                    ev_lat = float(ev_lat)
                    ev_lon = float(ev_lon)
                except ValueError:
                    continue

                # Calcul de la distance exacte
                dist = haversine_km(center_lat, center_lon, ev_lat, ev_lon)

                # mise à jour de la distance mini vue
                if min_distance is None or dist < min_distance:
                    min_distance = dist

                # Vérification finale du rayon (par sécurité, l'API devrait avoir déjà filtré)
                if dist > radius_km:
                    print(f"   ❌ Événement hors rayon: {dist:.1f}km > {radius_km}km")
                    continue

                total_events_after_distance += 1

                title_field = ev.get('title')
                if isinstance(title_field, dict):
                    ev_title = title_field.get('fr') or title_field.get('en') or 'Événement'
                else:
                    ev_title = title_field or 'Événement'

                # slug de l'événement
                event_slug = ev.get('slug')
                openagenda_url = None
                # Construction du lien public correct si on a les deux slugs
                if agenda_slug and event_slug:
                    openagenda_url = f"https://openagenda.com/{agenda_slug}/events/{event_slug}?lang=fr"

                all_events.append({
                    "uid": ev.get("uid"),
                    "title": ev_title,
                    "begin": begin_str,
                    "end": end_str,
                    "locationName": loc.get("name"),
                    "city": loc.get("city"),
                    "address": loc.get("address"),
                    "latitude": ev_lat,
                    "longitude": ev_lon,
                    "distanceKm": round(dist, 1),
                    "openagendaUrl": openagenda_url,
                    "agendaTitle": agenda_title,
                })

        # Tri par date de début (texte ISO)
        all_events.sort(key=lambda e: e["begin"] or "")

        debug_info = {
            "totalAgendas": total_agendas,
            "agendasWithEvents": agendas_with_events,
            "totalEventsScanned": total_events_scanned,
            "totalEventsAfterGeoFilter": total_events_after_geo_filter,
            "totalEventsAfterDistanceFilter": total_events_after_distance,
            "minDistanceKm": round(min_distance, 1) if min_distance is not None else None
        }

        print(f"✅ {len(all_events)} événements trouvés au total")
        print(f"📊 Debug: {debug_info}")

        return jsonify({
            "status": "success",
            "center": {"latitude": center_lat, "longitude": center_lon},
            "radiusKm": radius_km,
            "days": days_ahead,
            "events": all_events,
            "count": len(all_events),
            "debug": debug_info,
        }), 200

    except Exception as e:
        print("🔥 Error in /api/events/nearby:", repr(e))
        import traceback
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": "Une erreur interne est survenue dans /api/events/nearby.",
            "details": str(e),
        }), 500


# -------------------------------------------------
# Entrée principale
# -------------------------------------------------

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting server on port {port}")
    print(f"OpenAgenda BASE_URL={BASE_URL}")
    print(f"Radius default = {RADIUS_KM_DEFAULT} km, days default = {DAYS_AHEAD_DEFAULT}")
    app.run(host='0.0.0.0', port=port, debug=True)
 
