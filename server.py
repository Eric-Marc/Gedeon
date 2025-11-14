from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timezone
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

# Rayon (km) autour du téléphone
RADIUS_KM = 100

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
# Fonctions utilitaires : OpenAgenda
# -------------------------------------------------

def get_headers():
    """Entêtes HTTP pour l'API OpenAgenda."""
    return {
        "key": API_KEY,
        "Content-Type": "application/json"
    }


def search_agendas(search_term=None, official=None, limit=10):
    """
    Recherche d'agendas.
    - Si search_term est None : agendas associés à la clé API (sans filtre de ville).
    """
    url = f"{BASE_URL}/agendas"
    params = {"size": min(limit, 100)}

    if search_term:
        params["search"] = search_term
    if official is not None:
        params["official"] = 1 if official else 0

    try:
        r = requests.get(url, headers=get_headers(), params=params, timeout=10)
        r.raise_for_status()
        return r.json() or {}
    except requests.exceptions.RequestException as e:
        print(f"❌ Error searching agendas: {e}")
        return {"agendas": []}


def get_events_from_agenda(agenda_uid, limit=50):
    """
    Récupère les événements d'un agenda (current + upcoming),
    sans filtre de ville.
    """
    url = f"{BASE_URL}/agendas/{agenda_uid}/events"

    params = {
        "size": min(limit, 300),
        "detailed": 1,
        "relative[]": ["current", "upcoming"],
    }

    try:
        r = requests.get(url, headers=get_headers(), params=params, timeout=10)
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
# API : événements à proximité (rayon 100 km autour du téléphone)
# -------------------------------------------------

@app.route('/api/events/nearby', methods=['GET'])
def events_nearby():
    """
    Cherche des événements dans un rayon de 100 km autour du dernier point
    de localisation du téléphone.

    - Récupère les agendas accessibles via l'API (sans filtre de ville)
    - Récupère leurs événements (current + upcoming)
    - Pour chaque événement :
        * utilise location.latitude/longitude si présents
        * sinon géocode l'adresse avec Nominatim
        * calcule la distance au téléphone
        * garde seulement ceux à <= 100 km
    """
    try:
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

        # 2. Recherche d'agendas (sans filtre de ville)
        agendas_result = search_agendas(limit=30)
        agendas = agendas_result.get('agendas', []) if agendas_result else []

        if not agendas:
            return jsonify({
                "status": "success",
                "center": {"latitude": center_lat, "longitude": center_lon},
                "radiusKm": RADIUS_KM,
                "events": [],
                "count": 0,
                "info": "Aucun agenda trouvé pour cette clé API."
            }), 200

        # 3. Récupération des événements agenda par agenda + filtrage par distance
        all_events = []

        for agenda in agendas:
            uid = agenda.get('uid')
            title = agenda.get('title', {})
            if isinstance(title, dict):
                agenda_title = title.get('fr') or title.get('en') or 'Agenda'
            else:
                agenda_title = title or 'Agenda'

            events_data = get_events_from_agenda(uid, limit=200)
            events = events_data.get('events', []) if events_data else []
            if not events:
                continue

            for ev in events:
                timings = ev.get('timings') or []
                if not timings:
                    continue

                first_timing = timings[0]
                begin_str = first_timing.get('begin')
                begin_dt = parse_iso_datetime(begin_str)
                if not begin_dt:
                    continue

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
                    # on ajoute le pays pour aider Nominatim
                    parts.append("France")
                    address_str = ", ".join(parts)

                    geocoded_lat, geocoded_lon = geocode_address_nominatim(address_str)
                    if geocoded_lat is not None and geocoded_lon is not None:
                        ev_lat = geocoded_lat
                        ev_lon = geocoded_lon
                    else:
                        # Impossible de géocoder => on ignore cet événement pour la carte
                        continue

                try:
                    ev_lat = float(ev_lat)
                    ev_lon = float(ev_lon)
                except ValueError:
                    continue

                dist = haversine_km(center_lat, center_lon, ev_lat, ev_lon)
                if dist > RADIUS_KM:
                    continue

                title_field = ev.get('title')
                if isinstance(title_field, dict):
                    ev_title = title_field.get('fr') or title_field.get('en') or 'Événement'
                else:
                    ev_title = title_field or 'Événement'

                slug = ev.get('slug')
                openagenda_url = f"https://openagenda.com/e/{slug}" if slug else None

                all_events.append({
                    "uid": ev.get("uid"),
                    "title": ev_title,
                    "begin": begin_str,
                    "end": first_timing.get('end'),
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

        return jsonify({
            "status": "success",
            "center": {"latitude": center_lat, "longitude": center_lon},
            "radiusKm": RADIUS_KM,
            "events": all_events,
            "count": len(all_events),
        }), 200

    except Exception as e:
        print("🔥 Error in /api/events/nearby:", repr(e))
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
    print(f"Radius = {RADIUS_KM} km")
    app.run(host='0.0.0.0', port=port)
