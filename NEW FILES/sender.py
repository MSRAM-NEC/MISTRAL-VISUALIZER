import serial
import time
import logging
from pathlib import Path

logger = logging.getLogger("mmwave_sender")

def send_mmwave_config(cfg_file, config_port, config_baud=115200, delay=0.1):
    """
    Send TI mmWave .cfg file to the radar over the given config serial port.
    """
    try:
        cfg_path = Path(cfg_file)
        if not cfg_path.exists():
            logger.error(f"[ERROR] Config file not found: {cfg_path}")
            return False

        with serial.Serial(config_port, config_baud, timeout=1) as ser, open(cfg_path, "r") as f:
            logger.info(f"[INFO] Connected to {config_port} @ {config_baud}")
            for line in f:
                line = line.strip()
                if not line or line.startswith("%"):
                    continue

                ser.write((line + "\n").encode("ascii"))
                time.sleep(delay)
                resp = ser.read_all().decode(errors="ignore").strip()
                logger.info(f"Sent: '{line}' -> Resp: '{resp}'")

            logger.info("[INFO] Config file sent successfully.")
            return True
    except Exception as e:
        logger.error(f"[ERROR] Failed to send config: {e}")
        return False

