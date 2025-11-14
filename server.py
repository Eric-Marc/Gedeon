from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta
import json
import os
import math
import requests

# ------------------------------
# Configuration de l'application
# ------------------------------

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# Fichier local pour stocker l'historique des positions
DATA_FILE = 'locations.json'

# Configuration OpenAgenda
# Tu peux laisser ces valeurs en dur ou les surcharger via des variables d'environnement
API_KEY = os.environ.get("OPENAGENDA_API_KEY", "218909f158934e1badf3851a650ad6c1")
BASE_URL = os.environ.get("OPENAGENDA_BASE_URL", "https://api.openagenda.com/v2")

# Rayon de recherche des événements (en km)
RADIUS_KM = 30

# ------------------------------
# Fonctions utilitaires
# ------------------------------


def load_locations():
    """Charge la liste des positions depuis le fichier JSON."""
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
    """Sauvegarde la liste des positions dans le fichier JSON."""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(locations, f, ensure_ascii=False, indent=2)


def add_location(latitude, longitude, accuracy=None):
    """Ajoute une position à l'historique."""
    locations = load_locations()
    entry = {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "accuracy": float(accuracy) if accuracy is not None else None,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    locations.append(entry)
    save_locations(locations)
    return entry


def get_latest_location():
    """Retourne la dernière position enregistrée, ou None."""
    locations = load_locations()
    if not locations:
        return None
    return locations[-1]


def make_bbox(lat, lon, radius_km):
    """
    Construit un carré géographique (~rayon en km) autour d'un point.

    On utilise une approximation simple, largement suffisante pour un rayon de 30 km.
    """
    earth_radius_km = 6371.0

    # 1° de latitude ~ 111 km
    delta_lat = (radius_km / earth_radius_km) * (180.0 / math.pi)
    # 1° de longitude dépend de la latitude
    delta_lon = (radius_km / earth_radius_km) * (180.0 / math.pi) / math.cos(lat * math.pi / 180.0)

    return {
        "northEast": {"lat": lat + delta_lat, "lng": lon + delta_lon},
        "southWest": {"lat": lat - delta_lat, "lng": lon - delta_lon},
    }


# ------------------------------
# Routes front
# ------------------------------


@app.route('/')
def index():
    """Renvoie la page HTML principale."""
    return send_from_directory('.', 'index.html')


# ------------------------------
# API : positions
# ------------------------------


@app.route('/api/location', methods=['GET', 'POST', 'DELETE'])
def location_collection():
    """
    GET    -> renvoie toutes les positions
    POST   -> ajoute une nouvelle position
    DELETE -> efface toutes les positions
    """
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


@app.route('/api/location/latest', methods=['GET'])
def location_latest():
    """Renvoie la dernière position enregistrée."""
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


# ------------------------------
# API : événements OpenAgenda
# ------------------------------


@app.route('/api/events/nearby', methods=['GET'])
def events_nearby():
    """
    Renvoie les événements OpenAgenda dans un rayon de 30 km autour du
    dernier point enregistré, pour les deux jours à venir.

    Utilise la route transverse :
      GET /v2/events
    avec les filtres suivants :
      - timings[gte] / timings[lte]
      - geo[northEast][lat/lng], geo[southWest][lat/lng]
      - relative[]=current & relative[]=upcoming
      - state=2 (publié)
    """
    # Vérifie qu'on a une position
    latest = get_latest_location()
    if latest is None:
        return jsonify({
            "status": "error",
            "message": "Aucune position enregistrée, impossible de chercher des événements."
        }), 404

    lat = latest.get("latitude")
    lon = latest.get("longitude")
    if lat is None or lon is None:
        return jsonify({
            "status": "error",
            "message": "Dernier point invalide (latitude/longitude manquantes)"
        }), 500

    try:
        lat = float(lat)
        lon = float(lon)
    except ValueError:
        return jsonify({
            "status": "error",
            "message": "Coordonnées invalides"
        }), 500

    # Fenêtre temporelle : maintenant -> +2 jours
    now = datetime.utcnow()
    end = now + timedelta(days=2)

    # Zone géographique ~ rayon 30 km
    bbox = make_bbox(lat, lon, RADIUS_KM)

    # Paramètres API OpenAgenda (lecture transverse)
    params = {
        "timings[gte]": now.isoformat(timespec="seconds") + "Z",
        "timings[lte]": end.isoformat(timespec="seconds") + "Z",
        "relative[]": ["current", "upcoming"],
        "state": 2,
        "geo[northEast][lat]": bbox["northEast"]["lat"],
        "geo[northEast][lng]": bbox["northEast"]["lng"],
        "geo[southWest][lat]": bbox["southWest"]["lat"],
        "geo[southWest][lng]": bbox["southWest"]["lng"],
        "monolingual": "fr",
        "detailed": 1,
        "size": 50,
        "sort": "timings.asc",
    }

    url = f"{BASE_URL}/events"

    try:
        response = requests.get(
            url,
            headers={"key": API_KEY},
            params=params,
            timeout=10,
        )
    except requests.RequestException as e:
        return jsonify({
            "status": "error",
            "message": "Erreur de connexion à l'API OpenAgenda",
            "details": str(e),
        }), 502

    if not response.ok:
        return jsonify({
            "status": "error",
            "message": "Erreur API OpenAgenda",
            "details": response.text,
        }), response.status_code

    data = response.json()
    events = data.get("events", [])

    simplified_events = []
    for ev in events:
        location = ev.get("location") or {}
        timings = ev.get("timings") or []
        first_timing = timings[0] if timings else {}

        # Titre (multilingue possible)
        title_field = ev.get("title")
        if isinstance(title_field, dict):
            title = title_field.get("fr") or next(iter(title_field.values()), "")
        else:
            title = title_field or ""

        # URL OpenAgenda
        slug = ev.get("slug")
        openagenda_url = f"https://openagenda.com/e/{slug}" if slug else None

        simplified_events.append({
            "uid": ev.get("uid"),
            "title": title,
            "begin": first_timing.get("begin"),
            "end": first_timing.get("end"),
            "locationName": location.get("name"),
            "city": location.get("city"),
            "address": location.get("address"),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "openagendaUrl": openagenda_url,
        })

    return jsonify({
        "status": "success",
        "center": {"latitude": lat, "longitude": lon},
        "radiusKm": RADIUS_KM,
        "events": simplified_events,
    }), 200


# ------------------------------
# Entrée principale
# ------------------------------

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting server on port {port}")
    app.run(host='0.0.0.0', port=port)
