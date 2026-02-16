import os
import asyncio
from dotenv import load_dotenv
import signal

# Load environment variables
load_dotenv()

# SIP credentials
SIP_ID = os.getenv("SIP_ID")
SIP_PASS = os.getenv("SIP_PASS")
SIP_DOMAIN = os.getenv("SIP_DOMAIN")
SIP_PORT = os.getenv("SIP_PORT", "5060")
CALL_TIMEOUT_SECONDS = int(os.getenv("CALL_TIMEOUT_SECONDS", "45"))

# PJSUA_PATH can be overridden via .env per machine.
PJSUA_PATH = os.getenv(
    "PJSUA_PATH",
    "./pjproject-2.15.1/pjsip-apps/bin/pjsua-x86_64-unknown-linux-gnu",
    
)


async def make_call(destination_number: str, status_callback=None) -> asyncio.subprocess.Process:
    """
    Start a SIP call using pjsua asynchronously.
    Optionally accepts a status_callback(message: str) for real-time updates.
    """
    cmd = [
        PJSUA_PATH,
        "--id", f"sip:{SIP_ID}@{SIP_DOMAIN}",
        "--registrar", f"sip:{SIP_DOMAIN}",
        "--realm", "*",
        "--username", SIP_ID,
        "--password", SIP_PASS,
        "--local-port", "0",
        "--log-level", "5",
        f"sip:{destination_number}@{SIP_DOMAIN}"
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.PIPE
    )

    # Notify UI that calling started
    if status_callback:
        await status_callback(f" Calling {destination_number}...")

    # Start monitoring output asynchronously
    asyncio.create_task(_monitor_call_output(process, status_callback))
    asyncio.create_task(_auto_hangup_after_timeout(process, CALL_TIMEOUT_SECONDS, status_callback))

    return process


async def _monitor_call_output(process: asyncio.subprocess.Process, status_callback):
    """
    Monitor PJSUA output and notify via status_callback for call state.
    """
    try:
        async for line_bytes in process.stdout:
            line = line_bytes.decode(errors="ignore").strip()
            if not line:
                continue
            print("[PJSUA]", line)

            if status_callback:
                # Call connected
                if "CONFIRMED" in line:
                    await status_callback(" Call connected")
                # Call ended / disconnected
                elif "DISCONNECTED" in line or "Call ended" in line:
                    await status_callback(" Call ended")
    except Exception as e:
        print(f"[ERROR] Monitor exception: {e}")

    # Notify when process ends unexpectedly
    if status_callback:
        await status_callback(" Call process terminated")


async def _auto_hangup_after_timeout(
    process: asyncio.subprocess.Process,
    timeout_seconds: int,
    status_callback,
):
    """
    Enforce a max call duration to avoid hanging/ringing forever.
    """
    if timeout_seconds <= 0:
        return

    await asyncio.sleep(timeout_seconds)
    if process.returncode is None:
        await hangup_call(process)
        if status_callback:
            await status_callback(f" Call auto-ended after {timeout_seconds}s timeout")


async def hangup_call(process: asyncio.subprocess.Process) -> bool:
    """
    Hang up the call gracefully.
    """
    if not process or process.returncode is not None:
        return False

    try:
        # Try graceful hangup via stdin
        try:
            process.stdin.write(b"h\n")  # send bytes
            await process.stdin.drain()
        except Exception:
            pass

        # Wait briefly for process to exit
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
            return True
        except asyncio.TimeoutError:
            pass

        # Send SIGINT as fallback
        try:
            process.send_signal(signal.SIGINT)
            await asyncio.wait_for(process.wait(), timeout=2)
            return True
        except Exception:
            pass

        # Force kill if still running
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
