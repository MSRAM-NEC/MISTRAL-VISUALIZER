import serial
import time
from pathlib import Path

def send_mmwave_config(cfg_file, config_port, config_baud=115200, delay=0.1):
    """
    Send TI mmWave .cfg file to the radar over the given config serial port.
    """
    try:
        cfg_file = Path(cfg_file)
        if not cfg_file.exists():
            print(f"[ERROR] Config file not found: {cfg_file}")
            return False

        with serial.Serial(config_port, config_baud, timeout=1) as ser:
            print(f"[INFO] Connected to {config_port} @ {config_baud}")
            
            with open(cfg_file, "r") as f:
                lines = f.readlines()
            
            for line in lines:
                line = line.strip()
                if not line or line.startswith("%"):  # Skip comments/empty lines
                    continue

                cmd = line + "\n"
                ser.write(cmd.encode("ascii"))
                time.sleep(delay)  # Small delay between commands

                resp = ser.read_all().decode(errors="ignore").strip()
                if resp:
                    print(f"Sent: {line} -> Resp: {resp}")
                else:
                    print(f"Sent: {line}")
            
            print("[INFO] Config file sent successfully.")
            return True

    except Exception as e:
        print(f"[ERROR] Failed to send config: {e}")
        return False
