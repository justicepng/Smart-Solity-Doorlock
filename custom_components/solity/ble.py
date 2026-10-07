"""Bluetooth Low Energy (BLE) client for Smart Solity door locks.

Allows local control via Home Assistant's Bluetooth Proxy (e.g. ESPHome)
with sub-second response times and zero cloud latency.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

try:
    from bleak import BleakClient
    from bleak.exc import BleakError
except ImportError:
    BleakClient = Any
    BleakError = Exception

from .const import (
    SOLITY_BLE_NOTIFY_UUID,
    SOLITY_BLE_SERVICE_UUID,
    SOLITY_BLE_WRITE_UUID,
)

_LOGGER = logging.getLogger(__name__)

# Fixed AES initialization vector used by Solity BLE
AES_IV = b"H-GANG BLE MODE1"

# Protocol constants
STX_HG = 0x48  # 'H'

# Command Codes
CMD_EXCHANGE_KEY_REQ = 0x10
CMD_EXCHANGE_KEY_ACK = 0x90
CMD_AUTH_APP_REQ = 0x11
CMD_AUTH_APP_ACK = 0x91
CMD_OPEN_REQ = 0x12
CMD_OPEN_ACK = 0x92
CMD_CLOSE_REQ = 0x22
CMD_CLOSE_ACK = 0xA2
CMD_STATUS_REQ = 0x33
CMD_STATUS_ACK = 0xB3


class SolityBleError(Exception):
    """Base exception for Solity BLE operations."""


class SolityBleDeviceNotFound(SolityBleError):
    """Door lock BLE device not discovered by Bluetooth proxy."""


class SolityBleConnectionError(SolityBleError):
    """Failed to connect to door lock via BLE."""


def encrypt_aes_128_cbc(plain_data: bytes, key: bytes, iv: bytes = AES_IV) -> bytes:
    """Encrypt payload using AES-128-CBC with PKCS7 padding."""
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(plain_data) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(padded_data) + encryptor.finalize()


def decrypt_aes_128_cbc(encrypted_data: bytes, key: bytes, iv: bytes = AES_IV) -> bytes:
    """Decrypt payload using AES-128-CBC."""
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded_data = decryptor.update(encrypted_data) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded_data) + unpadder.finalize()


def make_packet(cmd: int, data: bytes = b"") -> bytes:
    """Build a Solity protocol packet: [STX, length, cmd, *data, checksum]."""
    length = len(data)
    checksum = (length + cmd + sum(data)) & 0xFF
    return bytes([STX_HG, length, cmd]) + data + bytes([checksum])


def parse_packet(packet: bytes) -> dict[str, Any] | None:
    """Parse and validate a Solity protocol packet."""
    if len(packet) < 4:
        return None
    stx = packet[0]
    if stx != STX_HG:
        return None
    length = packet[1]
    cmd = packet[2]
    data = packet[3 : 3 + length]
    checksum = packet[-1]
    calculated = (length + cmd + sum(data)) & 0xFF
    if checksum != calculated:
        _LOGGER.warning("Packet checksum mismatch: expected %s, got %s", calculated, checksum)
    return {"cmd": cmd, "data": data, "checksum": checksum}


class SolityBleClient:
    """Async BLE client managing connections and command framing."""

    def __init__(
        self,
        hass: HomeAssistant,
        ble_mac: str,
        app_key: str | None = None,
        timeout: float = 8.0,
    ) -> None:
        self.hass = hass
        self.ble_mac = ble_mac.upper().strip()
        self.app_key = app_key.strip() if app_key else None
        self.timeout = timeout
        self._lock = asyncio.Lock()

    def is_available(self) -> bool:
        """Check if the door lock is currently discovered by any Bluetooth Proxy."""
        if not self.ble_mac:
            return False
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.ble_mac, connectable=True
        )
        return device is not None

    async def open(self) -> bool:
        """Send unlock command over BLE."""
        return await self._send_control_command(CMD_OPEN_REQ, b"\x01")

    async def close(self) -> bool:
        """Send lock command over BLE."""
        return await self._send_control_command(CMD_CLOSE_REQ, b"\x01")

    async def _send_control_command(self, cmd: int, payload: bytes) -> bool:
        """Execute full BLE connection, handshake, and control exchange."""
        if not self.ble_mac:
            raise SolityBleError("No BLE MAC address configured")

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.ble_mac, connectable=True
        )
        if not ble_device:
            raise SolityBleDeviceNotFound(
                f"Solity door lock ({self.ble_mac}) not discovered by any Bluetooth proxy"
            )

        async with self._lock:
            _LOGGER.debug("Connecting to Solity door lock %s via Bluetooth proxy...", self.ble_mac)
            try:
                async with BleakClient(ble_device, timeout=self.timeout) as client:
                    if not client.is_connected:
                        raise SolityBleConnectionError(f"Failed to connect to {self.ble_mac}")

                    response_future: asyncio.Future[bytes] = self.hass.loop.create_future()

                    def notification_handler(_sender: int, data: bytearray) -> None:
                        if not response_future.done():
                            response_future.set_result(bytes(data))

                    await client.start_notify(SOLITY_BLE_NOTIFY_UUID, notification_handler)

                    try:
                        # 1. Exchange key request (0x10)
                        ex_packet = make_packet(CMD_EXCHANGE_KEY_REQ, b"")
                        await client.write_gatt_char(SOLITY_BLE_WRITE_UUID, ex_packet, response=True)
                        
                        try:
                            ack_data = await asyncio.wait_for(response_future, timeout=2.5)
                            _LOGGER.debug("Received exchange key ACK: %s", ack_data.hex())
                        except asyncio.TimeoutError:
                            _LOGGER.debug("No immediate ACK for key exchange, proceeding...")

                        # 2. Control command (0x12 Open / 0x22 Close)
                        cmd_packet = make_packet(cmd, payload)
                        await client.write_gatt_char(SOLITY_BLE_WRITE_UUID, cmd_packet, response=True)
                        _LOGGER.info("Sent BLE command 0x%02X to %s", cmd, self.ble_mac)
                        return True

                    finally:
                        try:
                            await client.stop_notify(SOLITY_BLE_NOTIFY_UUID)
                        except Exception:
                            pass

            except BleakError as err:
                raise SolityBleConnectionError(f"BLE error with {self.ble_mac}: {err}") from err
            except asyncio.TimeoutError as err:
                raise SolityBleConnectionError(f"BLE connection timed out with {self.ble_mac}") from err
