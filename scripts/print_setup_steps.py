#!/usr/bin/env python3
from __future__ import annotations
import os


def main() -> None:
    is_runpod = any(k in os.environ for k in ["RUNPOD_POD_ID", "RUNPOD_ID", "RUNPOD_TCP_PORT_8010"])
    print("Local Code Agent - Setup Helper")
    print("=" * 60)
    if is_runpod:
        print("Detected Runpod/VM environment. Choose one:")
        print("A) Public endpoint:")
        print("   - Expose port 8010 in your Runpod UI.")
        print("   - Set VSCode setting localCodeAgent.serverUrl to https://<runpod-url>")
        print("B) SSH tunnel (no public exposure):")
        print("   - On your local machine:")
        print("     ssh -L 8010:127.0.0.1:8010 <user>@<vm-host>")
        print("   - Set serverUrl to http://127.0.0.1:8010")
    else:
        print("Local mode:")
        print("1) Run: python3 bootstrap.py")
        print("2) Set VSCode setting localCodeAgent.serverUrl to http://127.0.0.1:8010")


if __name__ == "__main__":
    main()
