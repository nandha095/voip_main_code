import asyncio

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from make_call import hangup_call, make_call

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="SIP Call API")



app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or ["*"] for testing only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


active_call: asyncio.subprocess.Process | None = None
active_destination: str | None = None


class CallRequest(BaseModel):
    destination: str = Field(..., min_length=1, description="Destination SIP number")


@app.get("/")
async def root():
    return {
        "service": "sip-call-api",
        "status": "ok",
        "endpoints": ["/call", "/hangup", "/status"],
    }


@app.post("/call")
async def api_make_call(payload: CallRequest):
    global active_call, active_destination

    if active_call and active_call.returncode is None:
        raise HTTPException(
            status_code=409,
            detail=f"Call already active to {active_destination or 'unknown'}",
        )

    destination = payload.destination.strip()
    if not destination:
        raise HTTPException(status_code=422, detail="Destination cannot be empty")

    active_call = await make_call(destination, status_callback=None)
    if not active_call:
        raise HTTPException(status_code=500, detail="Failed to start call")

    active_destination = destination
    return {"status": "calling", "destination": destination}


@app.post("/hangup")
async def api_hangup_call():
    global active_call, active_destination

    if not active_call or active_call.returncode is not None:
        raise HTTPException(status_code=400, detail="No active call")

    success = await hangup_call(active_call)
    ended_destination = active_destination
    active_call = None
    active_destination = None

    if not success:
        raise HTTPException(status_code=500, detail="Failed to terminate active call cleanly")

    return {"status": "ended", "destination": ended_destination}


@app.get("/status")
async def api_status():
    if active_call and active_call.returncode is None:
        return {"status": "active", "destination": active_destination}
    return {"status": "idle"}
