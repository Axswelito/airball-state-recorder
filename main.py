from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import os
import base64
import logging
import phonenumbers
from phonenumbers import geocoder

# Initialize the FastAPI application
app = FastAPI()

# Configure basic logging to output informational messages for debugging and monitoring
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define a set of US states that require two-party consent for recording.
# Recording calls in these states without the consent of all parties involved can be illegal.
TWO_PARTY_STATES = {
    "CA", "DE", "FL", "IL", "MD", "MA", "MI",
    "MT", "NH", "OR", "PA", "WA", "CT"
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

# Retrieve the default recording behavior for calls where the state cannot be determined.
# This allows you to configure whether to record or skip in uncertain cases.
# The default is set to "skip" if the environment variable is not defined.
DEFAULT_RECORDING_BEHAVIOR = os.getenv("DEFAULT_RECORDING_DEFAULT_BEHAVIOR", "skip").lower()

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

async def get_call_recording_status(call_id: str) -> bool or None:
    """
    Fetches the current recording status of a specific call from the Aircall API.
    This is used to implement idempotency, ensuring we don't try to enable recording if it's already enabled.
    """
    if not AIRCALL_API_ID or not AIRCALL_API_TOKEN or not call_id:
        return None
    # Construct the Basic Auth credentials
    credentials = f"{AIRCALL_API_ID}:{AIRCALL_API_TOKEN}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        try:
            # Make a GET request to the Aircall API to retrieve call details
            response = await client.get(f"{AIRCALL_API_URL}/{call_id}", headers=headers)
            response.raise_for_status()  # Raise an exception for non-2xx status codes
            call_details = response.json()
            # Return the value of the 'recording' field from the API response
            return call_details.get("recording")
        except httpx.HTTPError as e:
            logging.error(f"Error fetching call details for ID {call_id}: {e}")
            return None
        except Exception as e:
            logging.error(f"Unexpected error fetching call details for ID {call_id}: {e}")
            return None

# Define the webhook endpoint that will handle incoming POST requests from Aircall
@app.post("/webhook")
async def handle_webhook(request: Request):
    # Process the incoming webhook request
    # Read the JSON payload sent by Aircall
    payload = await request.json()
    # Extract the 'data' part of the payload, which contains information about the call event
    call_data = payload.get("data", {})

    # Extract information about the Aircall phone number that received the call
    number_info = call_data.get("number", {})
    number_id = number_info.get("id")
    number_name = number_info.get("name")
    logging.info(f"📟 Aircall number info: ID={number_id}, Name={number_name}")

    # Extract the unique identifier of the call
    call_id = call_data.get("id")
    # Attempt to get the raw digits of the incoming phone number
    phone_number = call_data.get("raw_digits")

    # Fallback: If 'raw_digits' is not present, try to get the phone number from the 'participants' list
    if not phone_number:
        for p in call_data.get("participants", []):
            if p.get("type") == "external" and "phone_number" in p:
                phone_number = p["phone_number"]
                break

    logging.info(f"📞 Incoming call from {phone_number} with ID {call_id}")

    # Basic check for non-US phone numbers. We assume US numbers start with '+1'.
    # More robust validation might be needed depending on your use case.
    if not phone_number or not phone_number.startswith("+1"):
        logging.info(f"📞 Non-US phone number detected: {phone_number}. Skipping state-based recording logic.")
        return JSONResponse(content={"status": "non_us_number"}, status_code=200)

    # Determine the US state of the caller based on their phone number
    state = get_us_state_from_phone_number(phone_number)

    # Logic to handle call recording based on the caller's state
    # If the state is in the list of two-party consent states, do not record.
    if state in TWO_PARTY_STATES:
        logging.info(f"🔒 {state} is a 2-party consent state. Do NOT record call ID: {call_id}.")
        return JSONResponse(content={"recording": False, "state": state}, status_code=200)
    # If a US state was successfully determined and it's not a two-party consent state, attempt to enable recording.
    elif state and state not in TWO_PARTY_STATES:
        logging.info(f"✅ {state} is a 1-party consent state. Attempting to enable recording for call ID: {call_id}.")
        # Check if Aircall API credentials and the call ID are available to proceed with the API call.
        if AIRCALL_API_ID and AIRCALL_API_TOKEN and call_id:
            # Fetch the current recording status of the call to ensure idempotency.
            current_recording_status = await get_call_recording_status(call_id)
            # Only attempt to enable recording if it's not already enabled.
            if current_recording_status is not True:
                # Construct the Basic Auth credentials for the Aircall API.
                credentials = f"{AIRCALL_API_ID}:{AIRCALL_API_TOKEN}"
                encoded_credentials = base64.b64encode(credentials.encode()).decode()
                # Define the headers for the Aircall API request, including the Authorization header.
                headers = {
                    "Authorization": f"Basic {encoded_credentials}",
                    "Content-Type": "application/json"
                }
                # Use an asynchronous HTTP client to make the API call.
                async with httpx.AsyncClient() as client:
                    try:
                        # Send a PATCH request to the Aircall API to set the 'recording' status to True for the specific call.
                        response = await client.patch(
                            f"{AIRCALL_API_URL}/{call_id}",
                            headers=headers,
                            json={"recording": True}
                        )
                        # Log the response status code and body for debugging and monitoring.
                        logging.info(f"🔁 Aircall API response for call ID {call_id}: Status={response.status_code}, Body={response.text}")
                        # Log an error if the Aircall API returns a status code indicating a failure.
                        if response.status_code >= 400:
                            logging.error(f"⚠️ Aircall API error for call ID {call_id}: Status={response.status_code}, Body={response.text}")
                    # Handle potential HTTP errors during the API call (e.g., network issues).
                    except httpx.HTTPError as e:
                        logging.error(f"🚨 HTTP error while calling Aircall API for call ID {call_id}: {e}")
                    # Handle any other unexpected exceptions that might occur during the API call.
                    except Exception as e:
                        logging.error(f"🔥 An unexpected error occurred for call ID {call_id}: {e}")
            else:
                logging.info(f"📞 Call ID {call_id} is already being recorded. Skipping update.")
        # Return a JSON response indicating that recording should be enabled (or was already enabled).
        return JSONResponse(content={"recording": True, "state": state}, status_code=200)
    # If the US state could not be determined from the phone number.
    else:
        logging.warning(f"❌ Could not determine US state for phone number: {phone_number} (call ID: {call_id}). Default behavior: {DEFAULT_RECORDING_BEHAVIOR}")
        # Check the configured default recording behavior.
        if DEFAULT_RECORDING_BEHAVIOR == "record":
            # If the default behavior is to record even when the state is unknown.
            if AIRCALL_API_ID and AIRCALL_API_TOKEN and call_id:
                # Check the current recording status for idempotency.
                current_recording_status = await get_call_recording_status(call_id)
                if current_recording_status is not True:
                    # Construct API credentials and headers.
                    credentials = f"{AIRCALL_API_ID}:{AIRCALL_API_TOKEN}"
                    encoded_credentials = base64.b64encode(credentials.encode()).decode()
                    headers = {
                        "Authorization": f"Basic {encoded_credentials}",
                        "Content-Type": "application/json"
                    }
                    # Make the API call to enable recording.
                    async with httpx.AsyncClient() as client:
                        try:
                            response = await client.patch(
                                f"{AIRCALL_API_URL}/{call_id}",
                                headers=headers,
                                json={"recording": True}
                            )
                            logging.info(f"🔁 Aircall API response for call ID {call_id}: Status={response.status_code}, Body={response.text}")
                            if response.status_code >= 400:
                                logging.error(f"⚠️ Aircall API error for call ID {call_id}: Status={response.status_code}, Body={response.text}")
                        except httpx.HTTPError as e:
                            logging.error(f"🚨 HTTP error while calling Aircall API for call ID {call_id}: {e}")
                        except Exception as e:
                            logging.error(f"🔥 An unexpected error occurred for call ID {call_id}: {e}")
                else:
                    logging.info(f"📞 Call ID {call_id} is already being recorded (default behavior). Skipping update.")
            # Return a JSON response indicating recording is enabled and the state is unknown.
            return JSONResponse(content={"recording": True, "state": "unknown"}, status_code=200)
        else:
            # Return a JSON response indicating that the state was unknown and recording was skipped.
            return JSONResponse(content={"status": "unknown_state"}, status_code=200)
