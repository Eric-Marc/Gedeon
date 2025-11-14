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


def haversine_km(lat1, lon1, lat
