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

# === OpenAgenda (même logique que find_toulouse_events.py) ===
API_KEY = os.environ.get("OPENAGENDA_API_KEY", "218909f158934e1badf3851a650ad6c1")
BASE_URL = os.environ.get("OPENAGENDA_BASE_URL", "https://api.openagenda.com/v2")

# Ville utilisée pour chercher les agendas (comme ton script Toulouse)
DEFAULT_CITY = os.environ.get("OPENAGENDA_CITY", "Toulouse")

# Rayon (km)
RADIUS_KM = 100


# -------------------------------------------------
# Fonctions utilitaires : stockage des positions
# -------------------------------------------------

def load_locations():
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
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(locations, f, ensure_ascii=False, indent=2)


def add_location(latitude, longitude, accuracy=None):
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
    locations = load_locations()
    if not locations:
        return None
    return locations[-1]


# -------------------------------------------------
# Fonctions utilitaires : OpenAgenda
# -------------------------------------------------

def get_headers():
    """Entêtes OpenAgenda, comme dans ton script."""
    return {
        "key": API_KEY,
        "Content-Type": "application/json"
    }


def search_agendas(search_term=None, official=None, limit=10):
    """Recherche d'agendas (identique au script mais toujours un dict en retour)."""
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


def get_events_from_agenda(agenda_uid, limit=50, city=None):
    """Récupère les événements d'un agenda, avec city[] et current+upcoming (comme ton script)."""
    url = f"{BASE_URL}/agendas/{agenda_uid}/events"

    params = {
        "size": min(limit, 300),
        "detailed": 1,
        # comme ton script : on prend tout ce qui est en cours ou à venir
        "relative[]": ["current", "upcoming"],
    }
    if city:
        params["city[]"] = city

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
# Routes front
# -------------------------------------------------

@app.route('/')
def index():
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
# API : événements à proximité (multi-agendas, rayon 100 km)
# -------------------------------------------------

@app.route('/api/events/nearby', methods=['GET'])
def events_nearby():
    """
    Cherche des événements dans un rayon de 100 km autour du dernier point,
    en utilisant :
      - GET /v2/agendas
      - GET /v2/agendas/{uid}/events (relative: current+upcoming)
    puis filtrage par distance côté serveur.
    """
    try:
        # 1. Dernier point de localisation
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

        # 2. Recherche d'agendas comme ton script (ville "Toulouse" par défaut)
        agendas_result = search_agendas(search_term=DEFAULT_CITY, limit=30)
        agendas = agendas_result.get('agendas', []) if agendas_result else []

        if not agendas:
            return jsonify({
                "status": "success",
                "center": {"latitude": center_lat, "longitude": center_lon},
                "radiusKm": RADIUS_KM,
                "events": [],
                "city": DEFAULT_CITY,
                "count": 0,
                "info": "Aucun agenda trouvé pour cette recherche."
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

            events_data = get_events_from_agenda(uid, limit=200, city=DEFAULT_CITY)
            events = events_data.get('events', []) if events_data else []
            if not events:
                continue

            for ev in events:
                timings = ev.get('timings') or []
                if not timings:
                    continue

                first_timing = timings[0]
                begin_str = first_timing.get('begin')
                # On parse pour l'affichage éventuel, mais on ne filtre plus par date
                begin_dt = parse_iso_datetime(begin_str)
                if not begin_dt:
                    continue

                loc = ev.get('location') or {}
                ev_lat = loc.get('latitude')
                ev_lon = loc.get('longitude')

                if ev_lat is None or ev_lon is None:
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

        # 4. Tri par date de début (texte ISO)
        all_events.sort(key=lambda e: e["begin"] or "")

        return jsonify({
            "status": "success",
            "center": {"latitude": center_lat, "longitude": center_lon},
            "radiusKm": RADIUS_KM,
            "events": all_events,
            "city": DEFAULT_CITY,
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
    print(f"OpenAgenda city={DEFAULT_CITY}")
    print(f"Radius = {RADIUS_KM} km")
    app.run(host='0.0.0.0', port=port)
