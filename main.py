from fastapi import FastAPI, Request
import httpx
import os
import base64

app = FastAPI()

# Two-party consent states in the US
TWO_PARTY_STATES = {
    "CA", "DE", "FL", "IL", "MD", "MA", "MI",
    "MT", "NH", "OR", "PA", "WA", "CT"
}

# Area code to US state mapping
AREA_CODE_TO_STATE = {
    "213": "CA", "310": "CA", "415": "CA", "408": "CA", "818": "CA", "619": "CA", "510": "CA",
    "203": "CT", "860": "CT",
    "302": "DE",
    "305": "FL", "407": "FL", "561": "FL", "813": "FL", "904": "FL",
    "312": "IL", "773": "IL", "847": "IL",
    "301": "MD", "410": "MD", "443": "MD",
    "617": "MA", "781": "MA", "508": "MA",
    "313": "MI", "248": "MI", "734": "MI",
    "406": "MT",
    "603": "NH",
    "503": "OR", "541": "OR", "971": "OR",
    "215": "PA", "412": "PA", "717": "PA", "610": "PA",
    "206": "WA", "253": "WA", "425": "WA",
    "212": "NY", "646": "NY", "718": "NY",
    "214": "TX", "512": "TX", "713": "TX",
    "703": "VA", "804": "VA"
}

# Load credentials from environment
AIRCALL_API_ID = os.getenv("AIRCALL_API_ID")
AIRCALL_API_TOKEN = os.getenv("AIRCALL_API_TOKEN")
AIRCALL_API_URL = "https://api.aircall.io/v1/calls"

def extract_area_code(phone_number: str) -> str:
    if not phone_number:
        return None
    cleaned = (
        phone_number.replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
    )
    if cleaned.startswith("+1") and len(cleaned) > 4:
        return cleaned[2:5]
    return None

@app.post("/webhook")
async def handle_webhook(request: Request):
    payload = await request.json()
    call_data = payload.get("data", {})

    number_info = call_data.get("number", {})
    number_id = number_info.get("id")
    number_name = number_info.get("name")
    print(f"📟 Aircall number info: ID={number_id}, Name={number_name}")

    call_id = call_data.get("id")
    phone_number = call_data.get("raw_digits")

    if not phone_number:
        for p in call_data.get("participants", []):
            if p.get("type") == "external" and "phone_number" in p:
                phone_number = p["phone_number"]
                break

    print(f"📞 Incoming call from {phone_number} with ID {call_id}")

    area_code = extract_area_code(phone_number)
    state = AREA_CODE_TO_STATE.get(area_code)

    if not state:
        print("❌ State not found. Skipping recording.")
        return {"status": "unknown_state"}

    if state in TWO_PARTY_STATES:
        print(f"🔒 {state} is a 2-party consent state. Do NOT record.")
        return {"recording": False, "state": state}

    print(f"✅ {state} is a 1-party consent state. Enabling recording.")

    # Use Basic Auth (username = API ID, password = API TOKEN)
    if AIRCALL_API_ID and AIRCALL_API_TOKEN and call_id:
        credentials = f"{AIRCALL_API_ID}:{AIRCALL_API_TOKEN}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{AIRCALL_API_URL}/{call_id}",
                headers=headers,
                json={"recording": True}
            )
            print(f"🔁 Aircall API response: {response.status_code} - {response.text}")

    return {"recording": True, "state": state}
