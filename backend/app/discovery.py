"""Wi-Fi discovery: lets the phone app find this laptop server without a USB cable or typing an IP address.

The app broadcasts "VOICEGUARD?" on UDP port 8001 to its Wi-Fi network; this thread answers
"VOICEGUARD {"port": 8000, ...}". The phone takes the laptop's address from the reply packet itself, checks
/api/health on it and remembers it. (If the Wi-Fi blocks broadcasts, the app also tries the hotspot gateway and
scans its Wi-Fi subnet for /api/health.) Only the HTTP port is revealed - every API call still needs a login token.
"""
import json
import logging
import os
import socket
import threading

log = logging.getLogger("voiceguard.discovery")

DISCOVERY_PORT = int(os.getenv("VG_DISCOVERY_PORT", "8001"))
HTTP_PORT = int(os.getenv("VG_PORT", "8000"))
_started = False


def _serve() -> None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", DISCOVERY_PORT))
    except OSError as e:
        log.warning("Wi-Fi discovery off (UDP %s busy: %s) - phones can still connect by address", DISCOVERY_PORT, e)
        return
    reply = ("VOICEGUARD " + json.dumps({"port": HTTP_PORT, "name": socket.gethostname()})).encode()
    log.info("Wi-Fi discovery on UDP %s: phones on the same Wi-Fi find this server by themselves", DISCOVERY_PORT)
    while True:
        try:
            data, addr = s.recvfrom(256)
            if data.strip() == b"VOICEGUARD?":
                s.sendto(reply, addr)
        except OSError:
            continue


def start() -> None:
    global _started
    if _started or os.getenv("VG_DISCOVERY", "1") == "0":
        return
    _started = True
    threading.Thread(target=_serve, name="vg-discovery", daemon=True).start()


def lan_addresses() -> list[str]:
    """This laptop's IPv4 addresses on Wi-Fi / hotspot (for the start-up message)."""
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                out.add(ip)
    except OSError:
        pass
    return sorted(out)
