import os
import asyncio
import signal
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project folder (stable even if started from another cwd)
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

# SIP credentials
SIP_ID = os.getenv("SIP_ID")
SIP_PASS = os.getenv("SIP_PASS")
SIP_DOMAIN = os.getenv("SIP_DOMAIN")
SIP_PORT = os.getenv("SIP_PORT", "5060")

# Timeout settings:
# - RING_TIMEOUT_SECONDS: max wait for pickup
# - TALK_TIMEOUT_SECONDS: max conversation time after answer (0 = unlimited)
RING_TIMEOUT_SECONDS = int(os.getenv("RING_TIMEOUT_SECONDS", "45"))
TALK_TIMEOUT_SECONDS = int(os.getenv("TALK_TIMEOUT_SECONDS", "0"))

# pjsua binary path
PJSUA_PATH = os.getenv(
    "PJSUA_PATH",
    "./pjproject-2.15.1/pjsip-apps/bin/pjsua-x86_64-unknown-linux-gnu",
)


def _validate_config() -> None:
    missing = []
    if not SIP_ID:
        missing.append("SIP_ID")
    if not SIP_PASS:
        missing.append("SIP_PASS")
    if not SIP_DOMAIN:
        missing.append("SIP_DOMAIN")
    if not PJSUA_PATH:
        missing.append("PJSUA_PATH")

    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    if not os.path.isfile(PJSUA_PATH):
        raise RuntimeError(f"PJSUA binary not found at: {PJSUA_PATH}")

    if not os.access(PJSUA_PATH, os.X_OK):
        raise RuntimeError(f"PJSUA binary is not executable: {PJSUA_PATH}")


async def make_call(destination_number: str, status_callback=None) -> asyncio.subprocess.Process:
    """
    Start a SIP call using pjsua asynchronously.
    """
    _validate_config()

    answered_event = asyncio.Event()
    ended_event = asyncio.Event()

    cmd = [
        PJSUA_PATH,
        "--id", f"sip:{SIP_ID}@{SIP_DOMAIN}",
        "--registrar", f"sip:{SIP_DOMAIN}",
        "--realm", "*",
        "--username", SIP_ID,
        "--password", SIP_PASS,
        "--local-port", "0",
        "--log-level", "5",
        f"sip:{destination_number}@{SIP_DOMAIN}",
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.PIPE,
    )

    if status_callback:
        await status_callback(f"Calling {destination_number}...")

    asyncio.create_task(_monitor_call_output(process, status_callback, answered_event, ended_event))
    asyncio.create_task(_ring_timeout_guard(process, status_callback, answered_event, ended_event))
    asyncio.create_task(_talk_timeout_guard(process, status_callback, answered_event, ended_event))

    return process


async def _monitor_call_output(
    process: asyncio.subprocess.Process,
    status_callback,
    answered_event: asyncio.Event,
    ended_event: asyncio.Event,
):
    """
    Monitor pjsua output and notify call state.
    """
    try:
        async for line_bytes in process.stdout:
            line = line_bytes.decode(errors="ignore").strip()
            if not line:
                continue

            print("[PJSUA]", line)

            if "CONFIRMED" in line:
                answered_event.set()
                if status_callback:
                    await status_callback("Call connected")

            elif "DISCONNECTED" in line or "Call ended" in line:
                ended_event.set()
                if status_callback:
                    await status_callback("Call ended")
    except Exception as e:
        print(f"[ERROR] Monitor exception: {e}")
    finally:
        ended_event.set()
        if status_callback:
            await status_callback("Call process terminated")


async def _ring_timeout_guard(
    process: asyncio.subprocess.Process,
    status_callback,
    answered_event: asyncio.Event,
    ended_event: asyncio.Event,
):
    """
    End call if not answered within ring timeout.
    """
    if RING_TIMEOUT_SECONDS <= 0:
        return

    done, _ = await asyncio.wait(
        [asyncio.create_task(answered_event.wait()), asyncio.create_task(ended_event.wait())],
        timeout=RING_TIMEOUT_SECONDS,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Timeout happened (no answer, not ended)
    if not done and process.returncode is None:
        await hangup_call(process)
        if status_callback:
            await status_callback(f"Call ended: no answer within {RING_TIMEOUT_SECONDS}s")


async def _talk_timeout_guard(
    process: asyncio.subprocess.Process,
    status_callback,
    answered_event: asyncio.Event,
    ended_event: asyncio.Event,
):
    """
    If configured, limit conversation duration after answer.
    """
    if TALK_TIMEOUT_SECONDS <= 0:
        return

    await answered_event.wait()

    try:
        await asyncio.wait_for(ended_event.wait(), timeout=TALK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        if process.returncode is None:
            await hangup_call(process)
            if status_callback:
                await status_callback(f"Call auto-ended after {TALK_TIMEOUT_SECONDS}s talk time")


async def hangup_call(process: asyncio.subprocess.Process) -> bool:
    """
    Hang up the call gracefully.
    """
    if not process or process.returncode is not None:
        return False

    try:
        try:
            process.stdin.write(b"h\n")
            await process.stdin.drain()
        except Exception:
            pass

        try:
            await asyncio.wait_for(process.wait(), timeout=2)
            return True
        except asyncio.TimeoutError:
            pass

        try:
            process.send_signal(signal.SIGINT)
            await asyncio.wait_for(process.wait(), timeout=2)
            return True
        except Exception:
            pass

        process.kill()
        await process.wait()
        return True
    except Exception as e:
        print(f"[ERROR] Hangup failed: {e}")
        try:
            process.kill()
        except Exception:
            pass
        return False
