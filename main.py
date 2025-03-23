from fastapi import FastAPI, Request
import httpx
import os

app = FastAPI()

# Two-party consent states in the US
TWO_PARTY_STATES = {"CA", "FL", "IL", "MD", "MA", "MI", "MT", "NH", "PA", "WA"}

# Example area code map (you can expand this)
AREA_CODE_TO_STATE = {
    "212": "NY", "213": "CA", "305": "FL", "312": "IL",
    "617": "MA", "206": "WA", "818": "CA", "215": "PA",
    "646": "NY", "703": "VA", "773": "IL"
}

AIRCALL_API_KEY = os.getenv("AIRCALL_API_KEY")
AIRCALL_API_URL = "https://api.aircall.io/v1/calls"

def extract_area_code(phone_number: str) -> str:
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
