import os
import json
from datetime import datetime
from collections import Counter

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# -----------------------------------------------------------------------------
# Configuration de base
# -----------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "locations.json")
PING_FILE = os.path.join(BASE_DIR, "pings.json")


# -----------------------------------------------------------------------------
# Fonctions utilitaires pour lire / écrire les fichiers JSON
# -----------------------------------------------------------------------------

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # fichier corrompu → on repart de zéro
            return default
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_locations():
    return load_json(DATA_FILE, [])


def save_locations(locations):
    save_json(DATA_FILE, locations)


def load_pings():
    return load_json(PING_FILE, [])


def save_pings(pings):
    save_json(PING_FILE, pings)


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@app.route("/")
def index():
    """
    Sert la page web principale (index.html).
    Le fichier doit être à la racine du projet, à côté de server.py.
    """
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/ping", methods=["POST"])
def ping():
    """
    Enregistre chaque "connexion" (chargement de page) avec un client_id.
    Appelée automatiquement par le front au chargement de la page.
    """
    try:
        data = request.json or {}

        client_id = data.get("client_id", "unknown")
        user_agent = data.get("user_agent", request.headers.get("User-Agent", "unknown"))
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)

        pings = load_pings()
        entry = {
            "client_id": client_id,
            "ip": ip,
            "user_agent": user_agent,
            "timestamp": datetime.now().isoformat()
        }
        pings.append(entry)
        save_pings(pings)

        print(f"[PING] client_id={client_id} ip={ip}")
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print("[PING][ERROR]", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/location", methods=["POST"])
def receive_location():
    """
    Reçoit la position envoyée par le navigateur (téléphone, PC, etc.)
    et l'enregistre avec le client_id.
    """
    try:
        data = request.json or {}

        latitude = data.get("latitude")
        longitude = data.get("longitude")
        accuracy = data.get("accuracy")
        client_id = data.get("client_id", "unknown")

        if latitude is None or longitude is None:
            return jsonify({"error": "Missing latitude or longitude"}), 400

        ip = request.headers.get("X-Forwarded-For", request.remote_addr)

        locations = load_locations()

        entry = {
            "client_id": client_id,
            "latitude": latitude,
            "longitude": longitude,
            "accuracy": accuracy,
            "ip": ip,
            "timestamp": datetime.now().isoformat()
        }

        locations.append(entry)
        save_locations(locations)

        print(
            f"[LOC] client_id={client_id} ip={ip} "
            f"Lat={latitude} Lon={longitude} Acc={accuracy}m"
        )

        return jsonify({
            "status": "success",
            "message": "Location saved",
            "data": entry
        }), 200

    except Exception as e:
        print("[LOC][ERROR]", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/locations", methods=["GET"])
def get_locations():
    """
    Retourne toutes les positions enregistrées.
    """
    try:
        locations = load_locations()
        return jsonify({
            "status": "success",
            "count": len(locations),
            "locations": locations
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/location/latest", methods=["GET"])
def get_latest_location():
    """
    Retourne la dernière position connue (tous clients confondus).
    """
    try:
        locations = load_locations()
        if not locations:
            return jsonify({
                "status": "success",
                "message": "No locations stored yet"
            }), 200

        return jsonify({
            "status": "success",
            "location": locations[-1]
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/locations/clear", methods=["DELETE"])
def clear_locations():
    """
    Efface uniquement les positions du client_id fourni dans la requête.
    Ne supprime pas les positions des autres utilisateurs.
    """
    try:
        data = request.json or {}
        client_id = data.get("client_id")

        if not client_id:
            return jsonify({"error": "client_id is required"}), 400

        locations = load_locations()
        before = len(locations)
        locations = [loc for loc in locations if loc.get("client_id") != client_id]
        after = len(locations)
        removed = before - after

        save_locations(locations)

        print(f"[CLEAR] client_id={client_id} removed={removed}")

        return jsonify({
            "status": "success",
            "message": f"{removed} locations removed for client_id={client_id}",
            "removed": removed
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/users", methods=["GET"])
def list_users():
    """
    Liste les "utilisateurs" (client_id) distincts, avec le nombre de connexions
    d'après le fichier pings.json.
    """
    try:
        pings = load_pings()
        counts = Counter(p["client_id"] for p in pings)
        users = [
            {"client_id": cid, "connections": count}
            for cid, count in counts.items()
        ]
        return jsonify({"status": "success", "users": users}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -----------------------------------------------------------------------------
# Point d'entrée
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # Render fournit le port dans la variable d'environnement PORT
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting server on port {port}")
    app.run(host="0.0.0.0", port=port)
