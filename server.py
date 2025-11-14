from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime
import json
import os

app = Flask(__name__)
CORS(app)

# File to store location data
DATA_FILE = 'locations.json'

def load_locations():
    """Load locations from JSON file"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return []

def save_locations(locations):
    """Save locations to JSON file"""
    with open(DATA_FILE, 'w') as f:
        json.dump(locations, f, indent=2)

@app.route('/')
def index():
    """Serve the web app"""
    return send_from_directory('.', 'index.html')

@app.route('/api/location', methods=['POST'])
def receive_location():
    """Receive location data from the phone"""
    try:
        data = request.json
        latitude = data.get('latitude')
        longitude = data.get('longitude')
        accuracy = data.get('accuracy')

        if latitude is None or longitude is None:
            return jsonify({'error': 'Missing latitude or longitude'}), 400

        # Load existing locations
        locations = load_locations()

        # Add new location with timestamp
        location_entry = {
            'latitude': latitude,
            'longitude': longitude,
            'accuracy': accuracy,
            'timestamp': datetime.now().isoformat()
        }

        locations.append(location_entry)

        # Save updated locations
        save_locations(locations)

        print(f"Location received: Lat={latitude}, Lon={longitude}, Accuracy={accuracy}m")

        return jsonify({
            'status': 'success',
            'message': 'Location saved',
            'data': location_entry
        }), 200

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/locations', methods=['GET'])
def get_locations():
    """Get all stored locations"""
    try:
        locations = load_locations()
        return jsonify({
            'status': 'success',
            'count': len(locations),
            'locations': locations
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/location/latest', methods=['GET'])
def get_latest_location():
    """Get the most recent location"""
    try:
        locations = load_locations()
        if locations:
            return jsonify({
                'status': 'success',
                'location': locations[-1]
            }), 200
        else:
            return jsonify({
                'status': 'success',
                'message': 'No locations stored yet'
            }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/locations/clear', methods=['DELETE'])
def clear_locations():
    """Clear all stored locations"""
    try:
        save_locations([])
        return jsonify({
            'status': 'success',
            'message': 'All locations cleared'
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Render fournit le port dans la variable d'environnement PORT
    port = int(os.environ.get("PORT", 5000))
    print("Starting Location Tracking Server on port", port)

    # En prod, évite debug=True
    app.run(host='0.0.0.0', port=port)
