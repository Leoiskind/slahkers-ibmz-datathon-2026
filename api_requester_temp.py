# USE THIS TO REMEMBER HOW TO QUERY API


import requests
import json
from requests.auth import HTTPBasicAuth

# ==========================================
# API CONFIGURATION
# ==========================================

# API URL
URL = "https://hsp-prod.rockshore.net/api/v1/serviceMetrics"

# HTTP Method: GET, POST, PUT, PATCH, DELETE
METHOD = "POST"

# Basic Authentication
USERNAME = "daniel.mason-4@student.manchester.ac.uk"
PASSWORD = "Seska123."

# Request Headers
HEADERS = {
    "Content-Type": "application/json",
    "Content-Length": "178"
}

# Request Body (JSON)
BODY = {
    "from_loc": "EUS",
    "to_loc": "MAN",
    "from_time": "1100",
    "to_time": "1500",
    "from_date": "2026-10-16",
    "to_date": "2026-10-16",
    "days": "WEEKDAY"
}

# USE THIS FOR REMEMBERING HOW TO QUERY THE API


# Timeout in seconds
TIMEOUT = 30

# ==========================================
# SEND API REQUEST
# ==========================================

def call_api():
    try:
        response = requests.request(
            method=METHOD,
            url=URL,
            headers=HEADERS,
            json=BODY if METHOD.upper() not in ["GET", "HEAD"] else None,
            auth=HTTPBasicAuth(USERNAME, PASSWORD),
            timeout=TIMEOUT
        )

        print(f"Status Code: {response.status_code}")
        print(f"Response Time: {response.elapsed.total_seconds()}s")

        print("\nResponse Body:")
        try:
            print(json.dumps(response.json(), indent=4))
        except ValueError:
            print(response.text)

        response.raise_for_status()
        return response

    except requests.exceptions.RequestException as e:
        print(f"API Request Failed: {e}")
        return None


if __name__ == "__main__":
    call_api()
