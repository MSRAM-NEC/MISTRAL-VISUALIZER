import threading
import struct
import time
import queue
import logging
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import serial
except ImportError:
    serial = None

import math

MAGIC_WORD = b'\x02\x01\x04\x03\x06\x05\x08\x07'
TLV_HEADER_SIZE = 8

logger = logging.getLogger("mmwave_collector")

@dataclass
class MovingObject:
    """Dataclass to hold a single detected point from the radar."""
    x: float
    y: float
    z: float
    velocity: float
    rng: float
    snr: float
    timestamp: float = field(default_factory=time.time)

class SerialReadError(Exception):
    pass

class SerialCollector:
    """
    Background thread that reads from serial, finds mmWave packets,
    parses them, and queues the resulting points.
    """
    def __init__(self, data_port: str, data_baud: int = 921600, queue_max: int = 20000):
        if serial is None:
            raise SerialReadError("pyserial is required. Install with: pip install pyserial")

        self.data_port = data_port
        self.data_baud = data_baud
        self._ser_data: Optional[serial.Serial] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._data_q: queue.Queue = queue.Queue(maxsize=queue_max)
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def start(self):
        if self.running():
            logger.info("Collector already running")
            return
        try:
            self._ser_data = serial.Serial(self.data_port, self.data_baud, timeout=0.1)
            logger.info(f"Opened data serial {self.data_port} @ {self.data_baud}")
        except Exception as e:
            raise SerialReadError(f"Failed to open serial port {self.data_port}: {e}")

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        logger.info("Stopping collector")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._ser_data and self._ser_data.is_open:
            self._ser_data.close()
            logger.info(f"Closed data serial port {self.data_port}.")
        logger.info("Collector stopped")

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def get_latest(self, max_items: int = 2000) -> List[MovingObject]:
        items = []
        with self._lock:
            while len(items) < max_items:
                try:
                    items.append(self._data_q.get_nowait())
                except queue.Empty:
                    break
        return items

    def _read_loop(self):
        while not self._stop_event.is_set():
            try:
                if self._ser_data and self._ser_data.in_waiting > 0:
                    chunk = self._ser_data.read(self._ser_data.in_waiting)
                    self._buffer.extend(chunk)
                    self._process_buffer()
                else:
                    time.sleep(0.001) # Avoid busy-waiting
            except serial.SerialException as e:
                logger.error(f"Serial read error on {self.data_port}: {e}")
                self._stop_event.set()
            except Exception as e:
                logger.error(f"Error in read loop: {e}")

    def _process_buffer(self):
        while True:
            idx = self._buffer.find(MAGIC_WORD)
            if idx == -1:
                break
            if idx > 0:
                del self._buffer[:idx]

            try:
                header_len = struct.unpack_from('<I', self._buffer, 8)[0]
                total_packet_len = struct.unpack_from('<I', self._buffer, 12)[0]
            except struct.error:
                break

            if len(self._buffer) < total_packet_len:
                break

            packet = self._buffer[:total_packet_len]
            del self._buffer[:total_packet_len]

            objs = self._parse_packet(packet, header_len)
            if objs:
                with self._lock:
                    for o in objs:
                        if not self._data_q.full():
                            self._data_q.put_nowait(o)

    def _parse_packet(self, data: bytes, header_len: int) -> List[MovingObject]:
        points, side_info = [], []
        num_detected_obj = struct.unpack_from('<I', data, 28)[0]
        num_tlvs = struct.unpack_from('<I', data, 32)[0]
        offset = header_len

        for _ in range(num_tlvs):
            if offset + TLV_HEADER_SIZE > len(data): break
            tlv_type, tlv_length = struct.unpack_from('<II', data, offset)
            offset += TLV_HEADER_SIZE
            if offset + tlv_length > len(data): break
            tlv_data = data[offset : offset + tlv_length]

            if tlv_type == 1: # Detected Objects
                point_struct = struct.Struct('<ffff')
                for _ in range(num_detected_obj):
                    if len(tlv_data) < point_struct.size: break
                    x, y, z, v = point_struct.unpack(tlv_data[:point_struct.size])
                    points.append({'x': x, 'y': y, 'z': z, 'v': v})
                    tlv_data = tlv_data[point_struct.size:]

            elif tlv_type == 7: # Side Info (SNR)
                side_info_struct = struct.Struct('<hh')
                for _ in range(num_detected_obj):
                    if len(tlv_data) < side_info_struct.size: break
                    snr, _ = side_info_struct.unpack(tlv_data[:side_info_struct.size])
                    side_info.append(snr * 0.1)
                    tlv_data = tlv_data[side_info_struct.size:]
            offset += tlv_length

        moving_objects = []
        for i, p in enumerate(points):
            snr = side_info[i] if i < len(side_info) else 0
            rng = math.sqrt(p['x']**2 + p['y']**2 + p['z']**2)
            moving_objects.append(MovingObject(x=p['x'], y=p['y'], z=p['z'], velocity=p['v'], rng=rng, snr=snr))
        return moving_objects

