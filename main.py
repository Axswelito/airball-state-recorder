from fastapi import FastAPI, Request
import httpx
import os

app = FastAPI()

# Two-party consent states in the US
TWO_PARTY_STATES = {
    "CA", "DE", "FL", "IL", "MD", "MA", "MI",
    "MT", "NH", "OR", "PA", "WA", "CT"
}

# Example area code map (you can expand this)
AREA_CODE_TO_STATE = {
    # California (CA)
    "213": "CA", "310": "CA", "415": "CA", "408": "CA", "818": "CA", "619": "CA",
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
    
    # Extra (1-party states) for variety
    "212": "NY", "646": "NY", "718": "NY",    # New York
    "214": "TX", "512": "TX", "713": "TX",    # Texas
    "703": "VA", "804": "VA"                  # Virginia
}


AIRCALL_API_KEY = os.getenv("AIRCALL_API_KEY")
AIRCALL_API_URL = "https://api.aircall.io/v1/calls"

def extract_area_code(phone_number: str) -> str:
    if not phone_number:
        return None
    if phone_number.startswith("+1") and len(phone_number) > 4:
        return phone_number[2:5]
    return None

@app.post("/webhook")
async def handle_webhook(request: Request):
    data = await request.json()
    caller = data.get("caller_number")
    call_id = data.get("id")

    print(f"📞 Incoming call from {caller} with ID {call_id}")

    area_code = extract_area_code(caller)
    state = AREA_CODE_TO_STATE.get(area_code)

    if not state:
        print("❌ State not found. Skipping recording.")
        return {"status": "unknown_state"}

    if state in TWO_PARTY_STATES:
        print(f"🔒 {state} is a 2-party consent state. Do NOT record.")
        return {"recording": False, "state": state}

    # Enable recording if 1-party state
    print(f"✅ {state} is a 1-party consent state. Enabling recording.")
    if AIRCALL_API_KEY and call_id:
        headers = {"Authorization": f"Bearer {AIRCALL_API_KEY}"}
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{AIRCALL_API_URL}/{call_id}",
                headers=headers,
                json={"recording": True}
            )
            print(f"🔁 Aircall API response: {response.status_code} - {response.text}")

    return {"recording": True, "state": state}
