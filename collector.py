import threading
import struct
import time
import queue
import logging
from dataclasses import dataclass, field
from logging import Formatter 
from typing import List, Optional

try:
    import serial
except ImportError:
    serial = None

import math

MAGIC_WORD = b'\x02\x01\x04\x03\x06\x05\x08\x07'

logger = logging.getLogger("mmwave_collector")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(Formatter('%(asctime)s %(levelname)s: %(message)s'))
    logger.addHandler(ch)


@dataclass
class MovingObject:
    x: float
    y: float
    z: float
    velocity: float
    rng: float
    timestamp: float = field(default_factory=time.time)


class SerialReadError(Exception):
    pass


class SerialCollector:
    """
    Background thread that reads from serial, finds mmWave packets and TLVs,
    extracts TLV type 1 (detected objects) and queues them.
    """

    def __init__(self, data_port: str, data_baud: int = 921600, config_port: Optional[str] = None, config_baud: int = 115200, queue_max: int = 20000):
        if serial is None:
            raise SerialReadError("pyserial required. Install with pip install pyserial")

        self.data_port = data_port
        self.data_baud = data_baud
        self.config_port = config_port if config_port else data_port # Use data_port if config_port not provided
        self.config_baud = config_baud

        self._ser_data = None # Separate serial for data
        self._ser_config = None # Separate serial for config
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._data_q: queue.Queue = queue.Queue(maxsize=queue_max)
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.info("Collector already running")
            return

        try:
            # Open config port first
            self._ser_config = serial.Serial(self.config_port, self.config_baud, timeout=0.2)
            logger.info(f"Opened config serial {self.config_port} @ {self.config_baud}")

            # Open data port
            if self.data_port == self.config_port:
                # If ports are the same, use the same serial object, ensure baud is correct for data.
                self._ser_data = self._ser_config
                self._ser_data.baudrate = self.data_baud # Update baud rate if it's the data port
                logger.info(f"Data and Config ports are the same ({self.data_port}). Data baud rate set to {self.data_baud}.")
            else:
                self._ser_data = serial.Serial(self.data_port, self.data_baud, timeout=0.2)
                logger.info(f"Opened data serial {self.data_port} @ {self.data_baud}")

        except Exception as e:
            self.stop() # Ensure resources are cleaned up
            raise SerialReadError(f"Failed to open serial port(s): {e}")

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def send_config(self, config: List[str]):
        if not self._ser_config or not self._ser_config.is_open:
            raise SerialReadError("Configuration serial port not open.")

        logger.info("Sending configuration to the sensor...")
        for command in config:
            if not command.startswith('%') and command.strip():
                self._ser_config.write((command + '\n').encode('ascii'))
                time.sleep(0.1)
                response = self._ser_config.read_all().decode('ascii', errors="ignore")
                logger.info(f"Sent: {command.strip()} -> Recv: {response.strip()}")

        # After configuration, ensure the data port is set to the correct baud rate.
        # This is particularly important if data_port and config_port are the same,
        # and the config phase required a different baud rate (e.g., 115200) before
        # switching to a high baud rate (e.g., 921600) for data.
        if self._ser_data and self._ser_data.is_open:
            if self._ser_data.baudrate != self.data_baud:
                self._ser_data.baudrate = self.data_baud
                logger.info(f"Data serial baud rate updated to {self.data_baud}.")


    def stop(self):
        logger.info("Stopping collector")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0) # Wait for the thread to finish
        
        # Close serial ports
        if self._ser_data and self._ser_data.is_open:
            self._ser_data.close()
            logger.info(f"Closed data serial port {self.data_port}.")
        
        # Close config port only if it's distinct from data port
        if self._ser_config and self._ser_config.is_open and self._ser_config != self._ser_data:
            self._ser_config.close()
            logger.info(f"Closed config serial port {self.config_port}.")
        
        logger.info("Collector stopped")

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def get_latest(self, max_items: int = 1000) -> List[MovingObject]:
        """Drain up to max_items items from the queue and return them as a list."""
        items = []
        with self._lock:
            while len(items) < max_items:
                try:
                    items.append(self._data_q.get_nowait())
                except queue.Empty:
                    break
        return items

    def _read_loop(self):
        try:
            while not self._stop_event.is_set():
                try:
                    # Read from the data serial port
                    if self._ser_data:
                        chunk = self._ser_data.read(4096)
                        if chunk:
                            self._buffer.extend(chunk)
                            self._process_buffer()
                except serial.SerialException as e:
                    logger.error(f"Serial read error on {self.data_port}: {e}")
                    self._stop_event.set()  # Stop thread on serial error
                except Exception as e:
                    logger.error(f"Error in read loop: {e}")
                    time.sleep(0.1)
        except Exception as e:
            logger.exception(f"Collector thread crashed: {e}")
        finally:
            # Serial ports are closed in the stop() method, which is called after this loop ends.
            pass


    def _process_buffer(self):
        """Process buffer to find and parse mmWave packets."""
        while True:
            idx = self._buffer.find(MAGIC_WORD)

            if idx == -1:
                # If magic word not found, but buffer is too large, trim it
                if len(self._buffer) > 2 * 1024: # Keep a reasonable tail for potential partial magic word
                    self._buffer = self._buffer[-len(MAGIC_WORD) * 2:] # Keep enough to find a magic word
                break

            if idx > 0:
                # Trim anything before the magic word
                del self._buffer[:idx]

            if len(self._buffer) < 40: # Minimum header length
                break

            try:
                # totalPacketLen is at offset 12 from the start of the MAGIC_WORD
                total_packet_len = struct.unpack_from('<I', self._buffer, 12)[0]
            except struct.error:
                # If we can't even read the totalPacketLen, something is wrong with the header
                logger.warning("Could not unpack total_packet_len from header. Dropping current buffer segment.")
                del self._buffer[:len(MAGIC_WORD)] # Drop the problematic magic word
                continue

            if total_packet_len <= 0 or total_packet_len > 65536: # Sanity check on packet length
                logger.warning(f"Invalid total_packet_len={total_packet_len}; dropping magic word to avoid loop.")
                del self._buffer[:len(MAGIC_WORD)]
                continue

            if len(self._buffer) < total_packet_len:
                # Not enough data for the full packet yet
                break

            packet = self._buffer[:total_packet_len]
            del self._buffer[:total_packet_len]

            objs = self._parse_packet(packet)
            if objs:
                with self._lock: # Acquire lock before putting into queue
                    for o in objs:
                        try:
                            self._data_q.put_nowait(o)
                        except queue.Full:
                            # If queue is full, remove oldest and add new
                            try:
                                self._data_q.get_nowait()
                                self._data_q.put_nowait(o)
                            except queue.Empty:
                                pass # Should not happen right after get_nowait

    def _parse_packet(self, data: bytes) -> List[MovingObject]:
        moving = []
        header_len = 40 # Standard mmWave demo header length
        if len(data) < header_len:
            logger.warning("Packet too short to contain header.")
            return moving

        offset = header_len
        while offset + 8 <= len(data): # 8 bytes for TLV type and length
            try:
                tlv_type, tlv_length = struct.unpack_from('<II', data, offset)
            except struct.error:
                logger.warning(f"Could not unpack TLV header at offset {offset}. Remaining data length: {len(data) - offset}. Breaking.")
                break

            offset += 8

            if tlv_length == 0:
                logger.debug(f"TLV Type {tlv_type} has length 0. Skipping.")
                continue

            if offset + tlv_length > len(data):
                logger.warning(f"Incomplete TLV (type={tlv_type}, length={tlv_length}) found. Data ends at {len(data)}, expected {offset + tlv_length}. Breaking parse loop.")
                break

            tlv_data = data[offset: offset + tlv_length]

            if tlv_type == 1: # Detected Objects TLV
                # Object size is 16 bytes: x, y, z, velocity (4 floats)
                obj_size = 16
                for i in range(0, len(tlv_data), obj_size):
                    if i + obj_size > len(tlv_data):
                        logger.warning(f"Partial object data in TLV type 1. Remaining bytes: {len(tlv_data) - i}.")
                        break
                    try:
                        x, y, z, v = struct.unpack_from('<ffff', tlv_data, i)
                        rng = math.sqrt(x * x + y * y + z * z)
                        mo = MovingObject(round(x, 4), round(y, 4), round(z, 4),
                                          round(v, 4), round(rng, 4))
                        moving.append(mo)
                    except struct.error:
                        logger.warning(f"Could not unpack object data from TLV type 1 at sub-offset {i}.")
                        continue
            
            # Move to the next TLV
            offset += tlv_length
        return moving