from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

import httpx
import os
import base64
import logging
import phonenumbers
from phonenumbers import geocoder

# Initialize the FastAPI application
app = FastAPI()

processed_call_ids = set()

# Configure basic logging to output informational messages for debugging and monitoring
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define a set of US states that require two-party consent for recording.
# Recording calls in these states without the consent of all parties involved can be illegal.
TWO_PARTY_STATES = {
    "CALIFORNIA", "CONNECTICUT", "DELAWARE", "FLORIDA", "ILLINOIS", "MARYLAND",
    "MASSACHUSETTS", "MICHIGAN", "MONTANA", "NEW HAMPSHIRE", "OREGON", "PENNSYLVANIA", "WASHINGTON"
}

# Define a dictionary mapping US area codes to their corresponding states.
# This is used as a fallback mechanism to determine the caller's state.
# Note: This list might not be exhaustive or completely accurate due to area code splits and portability.
AREA_CODE_TO_STATE = {
    # California (CA)
    "213": "CA", "310": "CA", "415": "CA", "408": "CA", "818": "CA", "619": "CA", "510": "CA",
    # Connecticut (CT)
    "203": "CT", "860": "CT",
    # Delaware (DE)
    "302": "DE",
    # Florida (FL)
    "305": "FL", "407": "FL", "561": "FL", "813": "FL", "904": "FL",
    # Illinois (IL)
    "312": "IL", "773": "IL", "847": "IL",
    # Maryland (MD)
    "301": "MD", "410": "MD", "443": "MD",
    # Massachusetts (MA)
    "617": "MA", "781": "MA", "508": "MA",
    # Michigan (MI)
    "313": "MI", "248": "MI", "734": "MI",
    # Montana (MT)
    "406": "MT",
    # New Hampshire (NH)
    "603": "NH",
    # Oregon (OR)
    "503": "OR", "541": "OR", "971": "OR",
    # Pennsylvania (PA)
    "215": "PA", "412": "PA", "717": "PA", "610": "PA",
    # Washington (WA)
    "206": "WA", "253": "WA", "425": "WA",
    # 1-party states (examples)
    "212": "NY", "646": "NY", "718": "NY",
    "214": "TX", "512": "TX", "713": "TX",
    "703": "VA", "804": "VA"
}

# Retrieve Aircall API credentials and URL from environment variables.
# These should be configured in your Render deployment for security.
AIRCALL_API_ID = os.getenv("AIRCALL_API_ID")
AIRCALL_API_TOKEN = os.getenv("AIRCALL_API_TOKEN")
AIRCALL_API_URL = "https://api.aircall.io/v1/calls"

def get_us_state_from_phone_number(phone_number: str) -> str or None:
    """
    Attempts to determine the US state from a phone number using the `phonenumbers` library.
    It first tries to parse the number and get the state from the geolocation data.
    If that fails or doesn't provide a specific state, it falls back to the `AREA_CODE_TO_STATE` mapping.
    """
    if not phone_number:
        return None
    try:
        # Parse the phone number assuming it's a US number
        parsed_number = phonenumbers.parse(phone_number, "US")
        if not phonenumbers.is_valid_number(parsed_number):
            logging.warning(f"Invalid US phone number: {phone_number}")
            return None
        # Get the geographical description for the number (this might include the state)
        state_province = geocoder.description_for_number(parsed_number, "en")
        if state_province:
            # Extract the state abbreviation (assuming the last word is the state) and convert to uppercase
            return state_province.split()[-1].upper()
        else:
            # Fallback: Extract the area code and look up the state in our mapping
            area_code = str(phonenumbers.national_number(parsed_number))[:3]
            return AREA_CODE_TO_STATE.get(area_code)
    except phonenumbers.NumberParseException:
        logging.warning(f"Could not parse phone number: {phone_number}")
        return None
    except Exception as e:
        logging.error(f"An error occurred during phone number parsing: {e}")
        return None

# Define the webhook endpoint that will handle incoming POST requests from Aircall
@app.post("/webhook")
async def handle_webhook(request: Request):
    payload = await request.json()
    event = payload.get("event")
    logging.info(f"Received Aircall webhook event: {event}")

    if event == "call.answered":
        call_data = payload.get("data", {})

        number_info = call_data.get("number", {})
        number_id = number_info.get("id")
        number_name = number_info.get("name")
        logging.info(f"📟 Aircall number info: ID={number_id}, Name={number_name}")

        call_id = call_data.get("id")
        
        # Deduplication: skip if we've already processed this call ID
        if call_id in processed_call_ids:
            logging.info(f"🔁 Duplicate webhook received for call ID {call_id}. Skipping.")
            return JSONResponse(content={"status": "duplicate_skipped"}, status_code=200)
        # Mark this call ID as processed
        processed_call_ids.add(call_id)
        
        phone_number = call_data.get("raw_digits")

        if not phone_number:
            for p in call_data.get("participants", []):
                if p.get("type") == "external" and "phone_number" in p:
                    phone_number = p["phone_number"]
                    break

        logging.info(f"📞 Incoming call from {phone_number} with ID {call_id}")

        if not phone_number or not phone_number.startswith("+1"):
            logging.info(f"📞 Non-US phone number detected: {phone_number}. Skipping state-based recording logic.")
            return JSONResponse(content={"status": "non_us_number"}, status_code=200)

        state = get_us_state_from_phone_number(phone_number)

        if state in TWO_PARTY_STATES or state is None:
            consent_type = "2-party" if state in TWO_PARTY_STATES else "unknown"
            logging.info(f"🔒 {consent_type} consent state detected (or state not identified). Attempting to pause recording for call ID: {call_id}.")
            if AIRCALL_API_ID and AIRCALL_API_TOKEN and call_id:
                credentials = f"{AIRCALL_API_ID}:{AIRCALL_API_TOKEN}"
                encoded_credentials = base64.b64encode(credentials.encode()).decode()
                headers = {
                    "Authorization": f"Basic {encoded_credentials}",
                    "Content-Type": "application/json"
                }
                pause_recording_url = f"{AIRCALL_API_URL}/{call_id}/pause_recording"
                async with httpx.AsyncClient() as client:
                    try:
                        response = await client.post(pause_recording_url, headers=headers)
                        logging.info(f"⏸️ Aircall API response for pausing recording on call ID {call_id}: Status={response.status_code}, Body={response.text}")

                        if response.status_code == 204:
                            logging.info(f"✅ Successfully paused recording for call ID {call_id} (204 No Content)")
                            return Response(status_code=204)  # Empty body
                        else:
                            return JSONResponse(
                                content={"recording": "paused", "state": state},
                                status_code=response.status_code
                            )

                    except httpx.HTTPError as e:
                        logging.error(f"🚨 HTTP error while calling Aircall API to pause recording on call ID {call_id}: {e}")
                        return JSONResponse(content={"error": str(e)}, status_code=500)
                    except Exception as e:
                        logging.error(f"🔥 An unexpected error occurred while pausing recording on call ID {call_id}: {e}")
                        return JSONResponse(content={"error": str(e)}, status_code=500)
            else:
                logging.warning("Aircall API credentials or call ID not available to pause recording.")
                return JSONResponse(content={"status": "credentials_missing"}, status_code=500)
        else:
            logging.info(f"✅ {state} is a 1-party consent state. Recording will continue as default for call ID: {call_id}.")
            return JSONResponse(content={"recording": "active", "state": state}, status_code=200)
    else:
        return JSONResponse(content={"status": "ignored_event"}, status_code=200)
