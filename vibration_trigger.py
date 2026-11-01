#!/usr/bin/env python3
import os
import time
import subprocess
from gpiozero import DigitalInputDevice

GPIO_PIN = 22           # BCM Pin 22
COOLDOWN_SECONDS = 5.0  # Debounce delay between triggers
HOME = os.path.expanduser("~")

last_trigger_time = 0
current_process = None

def on_vibration():
    global last_trigger_time, current_process
    now = time.time()
    
    # 1. Check if terminal-soliloquy is already active
    if current_process is not None and current_process.poll() is None:
        print(f"[{time.strftime('%H:%M:%S')}] Ignored trigger: terminal-soliloquy is already running.")
        return

    # 2. Check debounce window
    if (now - last_trigger_time) < COOLDOWN_SECONDS:
        return
    last_trigger_time = now

    print(f"[{time.strftime('%H:%M:%S')}] Vibration detected! Launching terminal-soliloquy...")
    
    # 3. Store process handle to monitor execution
    current_process = subprocess.Popen([
        "/usr/bin/foot",
        "-D", f"{HOME}/terminal-soliloquy",
        "--fullscreen",
        f"{HOME}/terminal-soliloquy/.venv/bin/terminal-soliloquy"
    ])

def main():
    sensor = DigitalInputDevice(GPIO_PIN, pull_up=False)
    sensor.when_activated = on_vibration
    sensor.when_deactivated = on_vibration

    print(f"Vibration listener active on GPIO {GPIO_PIN}...")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
