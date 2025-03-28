from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import os
import base64
import logging
import phonenumbers
from phonenumbers import geocoder

# Initialize FastAPI application
app = FastAPI()

# Configure logging to output informational messages with timestamps and formatting
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Set of US states requiring two-party consent for recording conversations
TWO_PARTY_STATES = {
    "CA", "DE", "FL", "IL", "MD", "MA", "MI",
    "MT", "NH", "OR", "PA", "WA", "CT"
}

# Area code to US state mapping (Note: This dictionary is not exhaustive and may not be completely accurate)
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

# Retrieve Aircall API credentials from environment variables set in Render
AIRCALL_API_ID = os.getenv("AIRCALL_API_ID")
AIRCALL_API_TOKEN = os.getenv("AIRCALL_API_TOKEN")
# Define the base URL for the Aircall API calls endpoint
AIRCALL_API_URL = "https://api.aircall.io/v1/calls"

# Retrieve the default recording behavior for unknown states from environment variables
# Defaults to "skip" if the variable is not set
DEFAULT_RECORDING_BEHAVIOR = os.getenv("DEFAULT_RECORDING_BEHAVIOR", "skip").lower()  # Options: record, skip

def get_us_state_from_phone_number(phone_number: str) -> str or None:
    """
    Attempts to determine the US state from a phone number using the phonenumbers library
    and falls back to area code mapping if the library doesn't provide a specific state.
    """
    # Check if the phone number is empty or None
    if not phone_number:
        return None
    try:
        # Parse the phone number assuming it's a US number
        parsed_number = phonenumbers.parse(phone_number, "US")
        # Check if the parsed number is a valid phone number
        if not phonenumbers.is_valid_number(parsed_number):
            logging.warning(f"Invalid US phone number: {phone_number}")
            return None
        # Get the geographical description (which might include the state) for the phone number
        state_province = geocoder.description_for_number(parsed_number, "en")
        # If a state/province is found
        if state_province:
            # Basic cleaning - geocoder might return "State of California", so we take the last part and convert to uppercase
            return state_province.split()[-1].upper()
        else:
            # Fallback to area code mapping if the geocoder doesn't provide a state
            area_code = str(phonenumbers.national_number(parsed_number))[:3]
            return AREA_CODE_TO_STATE.get(area_code)
    # Handle exceptions that might occur during phone number parsing
    except phonenumbers.NumberParseException:
        logging.warning(f"Could not parse phone number: {phone_number}")
        return None
    except Exception as e:
        logging.error(f"An error occurred during phone number parsing: {e}")
        return None

# Define the webhook endpoint that Aircall will send call events to
@app.post("/webhook")
async def handle_webhook(request: Request):
    # Read the JSON payload from the incoming webhook request
    payload = await request.json()
    # Extract the 'data' part of the payload, which contains call-specific information
    call_data = payload.get("data", {})

    # Extract information about the Aircall phone number that received the call
    number_info = call_data.get("number", {})
    number_id = number_info.get("id")
    number_name = number_info.get("name")
    logging.info(f"📟 Aircall number info: ID={number_id}, Name={number_name}")

    # Extract the unique ID of the call
    call_id = call_data.get("id")
    # Attempt to get the raw digits of the incoming phone number
    phone_number = call_data.get("raw_digits")

    # Fallback to get the phone number from the 'participants' list if 'raw_digits' is missing
    if not phone_number:
        for p in call_data.get("participants", []):
            if p.get("type") == "external" and "phone_number" in p:
                phone_number = p["phone_number"]
                break

    logging.info(f"📞 Incoming call from {phone_number} with ID {call_id}")

    # Basic check for non-US numbers (you might need more sophisticated logic based on your requirements)
    if not phone_number or not phone_number.startswith("+1"):
        logging.info(f"📞 Non-US phone number detected: {phone_number}. Skipping state-based recording logic.")
        return JSONResponse(content={"status": "non_us_number"}, status_code=200)

    # Attempt to determine the US state based on the phone number
    state = get_us_state_from_phone_number(phone_number)

    # Check if the determined state is in the list of two-party consent states
    if state in TWO_PARTY_STATES:
        logging.info(f"🔒 {state} is a 2-party consent state. Do NOT record call ID: {call_id}.")
        return JSONResponse(content={"recording": False, "state": state}, status_code=200)
    # If a state was successfully determined and it's not a two-party consent state
    elif state:
        logging.info(f"✅ {state} is a 1-party consent state. Attempting to enable recording for call ID: {call_id}.")
        # Check if Aircall API credentials and call ID are available
        if AIRCALL_API_ID and AIRCALL_API_TOKEN and call_id:
            # Construct the basic authentication credentials string
            credentials = f"{AIRCALL_API_ID}:{AIRCALL_API_TOKEN}"
            # Encode the credentials string to Base64
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            # Set the authorization header for the Aircall API request
            headers = {
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/json"
            }

            # Use an asynchronous HTTP client to make the API call
            async with httpx.AsyncClient() as client:
                try:
                    # Send a PATCH request to the Aircall API to enable recording for the specific call
                    response = await client.patch(
                        f"{AIRCALL_API_URL}/{call_id}",
                        headers=headers,
                        json={"recording": True}
                    )
                    # Log the response status code and body from the Aircall API
                    logging.info(f"🔁 Aircall API response for call ID {call_id}: Status={response.status_code}, Body={response.text}")
                    # Log an error if the Aircall API returns a status code indicating an issue
                    if response.status_code >= 400:
                        logging.error(f"⚠️ Aircall API error for call ID {call_id}: Status={response.status_code}, Body={response.text}")
                        # Consider returning an error response to Aircall if this is a critical failure
                # Handle potential HTTP errors during the API call
                except httpx.HTTPError as e:
                    logging.error(f"🚨 HTTP error while calling Aircall API for call ID {call_id}: {e}")
                # Handle any other unexpected exceptions
                except Exception as e:
                    logging.error(f"🔥 An unexpected error occurred for call ID {call_id}: {e}")
        # Return a JSON response indicating that recording should be enabled (even if the API call failed, for simplicity in this example)
        return JSONResponse(content={"recording": True, "state": state}, status_code=200)
    # If the US state could not be determined
    else:
        logging.warning(f"❌ Could not determine US state for phone number: {phone_number} (call ID: {call_id}). Default behavior: {DEFAULT_RECORDING_BEHAVIOR}")
        # Check the configured default recording behavior
        if DEFAULT_RECORDING_BEHAVIOR == "record":
            # Attempt to enable recording if the default behavior is set to "record"
            if AIRCALL_API_ID and AIRCALL_API_TOKEN and call_id:
                credentials = f"{AIRCALL_API_ID}:{AIRCALL_API_TOKEN}"
                encoded_credentials = base64.b64encode(credentials.encode()).decode()
                headers = {
                    "Authorization": f"Basic {encoded_credentials}",
                    "Content-Type": "application/json"
                }

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
            # Return a JSON response indicating recording is enabled and the state is unknown
            return JSONResponse(content={"recording": True, "state": "unknown"}, status_code=200)
        else:
            # Return a JSON response indicating that the state was unknown and recording was skipped
            return JSONResponse(content={"status": "unknown_state"}, status_code=200)

# Note on Idempotency:
# To make this idempotent, before calling the Aircall API to set recording to True,
# you would ideally fetch the current call details from the Aircall API
# (using a GET request to /v1/calls/{call_id}) and check the current 'recording' status.
# Only proceed with the PATCH request if the current status is not already True.

# Reminder on Asynchronous Operations:
# FastAPI and httpx are asynchronous. Ensure your deployment environment (Render)
# is configured to handle asynchronous requests efficiently.
