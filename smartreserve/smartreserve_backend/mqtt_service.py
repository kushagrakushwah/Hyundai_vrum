"""
Hyundai SmartReserve — Asynchronous MQTT Service
Bridges Mosquitto MQTT broker with FastAPI event loop, database, and WebSockets.
Subscribes to:
  - evsarthi/+/+/state
  - evsarthi/+/+/telemetry
  - evsarthi/+/+/auth
Publishes to:
  - evsarthi/{station}/{connector}/reserve
  - evsarthi/{station}/{connector}/cmd
"""
import asyncio
import json
import logging
import math
import os
import time
from typing import Any, Callable, Dict, List, Optional, Union

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

import database
from websocket_manager import ws_manager

logger = logging.getLogger("smartreserve.mqtt")
logging.basicConfig(level=logging.INFO)


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Coerces val to a finite float, defaulting on None, parse errors, NaN, or Inf."""
    try:
        if val is None:
            return default
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError, OverflowError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    """Coerces val to a finite integer, defaulting on None, parse errors, NaN, or Inf."""
    try:
        if val is None:
            return default
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return int(f)
    except (ValueError, TypeError, OverflowError):
        return default


class CallableBool(int):
    """Allows is_connected to be checked as a boolean property or called as a method."""
    def __call__(self) -> bool:
        return bool(self)


class MQTTService:
    def __init__(self):
        self.broker_host = os.getenv("MQTT_BROKER_HOST", "localhost")
        self.broker_port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
        self.keepalive = int(os.getenv("MQTT_KEEPALIVE", "60"))
        self.client_id = os.getenv("MQTT_CLIENT_ID", f"smartreserve_backend_{os.getpid()}")

        self.client: Optional[mqtt.Client] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._connected: bool = False
        self._custom_listeners: Dict[str, List[Callable]] = {}

    @property
    def is_connected(self) -> CallableBool:
        return CallableBool(1 if self._connected else 0)

    async def start(self):
        """Initialize and start the background MQTT client on FastAPI startup."""
        self._loop = asyncio.get_running_loop()
        try:
            self.client = mqtt.Client(
                callback_api_version=CallbackAPIVersion.VERSION2,
                client_id=self.client_id
            )
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

            # Exponential backoff reconnection: min 1s, max 30s
            self.client.reconnect_delay_set(min_delay=1, max_delay=30)

            logger.info(f"[MQTT] Connecting to broker at {self.broker_host}:{self.broker_port}...")
            self.client.connect_async(self.broker_host, self.broker_port, self.keepalive)
            self.client.loop_start()
        except Exception as e:
            logger.warning(
                f"[MQTT] Could not connect to broker at {self.broker_host}:{self.broker_port} ({e}). "
                "Operating in offline/simulation fallback mode."
            )

    async def stop(self):
        """Gracefully disconnect and stop background loop on FastAPI shutdown."""
        if self.client:
            logger.info("[MQTT] Stopping MQTT client...")
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception as e:
                logger.error(f"[MQTT] Error stopping client: {e}")
            self._connected = False

    # ── Internal Paho Callbacks (Running on Paho background thread) ─────────

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self._connected = True
            logger.info(f"✅ [MQTT] Connected to broker ({self.broker_host}:{self.broker_port})")
            # Subscribe to all 3 inbound topics with wildcards
            subscriptions = [
                ("evsarthi/+/+/state", 1),
                ("evsarthi/+/+/telemetry", 0),
                ("evsarthi/+/+/auth", 1),
            ]
            client.subscribe(subscriptions)
            logger.info("📡 [MQTT] Subscribed to state, telemetry, and auth topics")
        else:
            logger.error(f"❌ [MQTT] Connection refused with code: {reason_code}")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        self._connected = False
        logger.warning(f"⚠️ [MQTT] Disconnected from broker (rc={reason_code}). Automatic reconnect active.")

    def _on_message(self, client, userdata, msg):
        """Synchronous callback on Paho thread. Bridges message into asyncio loop."""
        if not self._loop or not self._loop.is_running():
            return

        topic = msg.topic
        payload_bytes = msg.payload
        future = asyncio.run_coroutine_threadsafe(
            self._async_handle_message(topic, payload_bytes),
            self._loop
        )
        future.add_done_callback(self._future_exception_handler)

    def _future_exception_handler(self, fut):
        try:
            fut.result()
        except Exception as exc:
            logger.error(f"[MQTT] Exception handling incoming message: {exc}", exc_info=True)

    # ── Async Dispatch & Topic Handlers ─────────────────────────────────────

    async def _async_handle_message(self, topic: str, payload_bytes: bytes):
        """Parses topic and dispatches payload to corresponding async handler."""
        parts = topic.split('/')
        if len(parts) != 4 or parts[0] != "evsarthi":
            return

        _, station_id, connector_id, subtopic = parts
        try:
            payload = json.loads(payload_bytes.decode('utf-8'))
        except Exception as e:
            logger.error(f"[MQTT] Failed to decode JSON from {topic}: {e}")
            return

        if not isinstance(payload, dict):
            logger.warning(f"[MQTT] Ignored non-dict JSON payload from {topic}: {type(payload).__name__}")
            return

        if subtopic == "state":
            await self._handle_state(station_id, connector_id, payload)
        elif subtopic == "telemetry":
            await self._handle_telemetry(station_id, connector_id, payload)
        elif subtopic == "auth":
            await self._handle_auth(station_id, connector_id, payload)

        # Notify any custom listeners registered by tests or M3 CSMS bridge
        for pattern, listeners in list(self._custom_listeners.items()):
            if self._topic_matches(pattern, topic):
                for listener in listeners:
                    try:
                        res = listener(topic, payload)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as e:
                        logger.error(f"[MQTT] Error in custom listener: {e}")

    async def _handle_state(self, station_id: str, connector_id: str, payload: dict):
        """Processes evsarthi/{station}/{connector}/state."""
        if not isinstance(payload, dict):
            logger.warning(f"[MQTT] _handle_state received non-dict payload: {type(payload).__name__}")
            return

        state = str(payload.get("state") or "AVAILABLE")
        reason = str(payload.get("transition_reason") or "")
        relay_state = str(payload.get("relay_state") or "DE_ENERGIZED")
        uptime_millis = _safe_int(payload.get("uptime_millis"), 0)

        logger.info(f"🔄 [MQTT STATE] Station={station_id} Conn={connector_id} -> {state} ({reason})")

        # Save latest connector status in database cache
        await database.save_connector_status(station_id, connector_id, payload)

        # Handle reservation timeout / expired transition
        if state == "AVAILABLE" and ("timeout" in reason.lower() or "expired" in reason.lower()):
            res = await database.get_reservation(station_id, connector_id)
            if res:
                await database.delete_reservation(station_id, connector_id)
                await database.save_booking_history({
                    **res,
                    "outcome": "EXPIRED_NO_SHOW",
                    "total_kwh": 0,
                    "amount_charged_rs": 0
                })
                await ws_manager.broadcast_to_kiosk(station_id, {
                    "type": "OCPP_RESERVATION_EXPIRED",
                    "station_id": station_id,
                    "connector_id": connector_id,
                    "reason": "60-second hardware countdown expired",
                    "timestamp": time.time()
                })
                user_id = res.get("user_id")
                if user_id:
                    await ws_manager.broadcast_to_car(user_id, {
                        "type": "RESERVATION_EXPIRED",
                        "station_id": station_id,
                        "connector_id": connector_id,
                        "reason": "60-second hardware countdown expired"
                    })

        # Handle hardware fault trip
        elif state == "FAULTED":
            session = await database.get_session(station_id, connector_id)
            if session:
                await database.delete_session(station_id, connector_id)
                await database.save_booking_history({
                    "reservation_id": session.get("session_id"),
                    "station_id": station_id,
                    "connector_id": connector_id,
                    "user_id": session.get("user_id"),
                    "user_name": "EV Driver",
                    "created_at": session.get("start_time", time.time()),
                    "expires_at": time.time(),
                    "outcome": "HARDWARE_FAULT_TRIP",
                    "total_kwh": session.get("kwh", 0.0),
                    "amount_charged_rs": 0
                })
                user_id = session.get("user_id")
                if user_id:
                    await ws_manager.broadcast_to_car(user_id, {
                        "type": "CHARGING_FAULT",
                        "station_id": station_id,
                        "connector_id": connector_id,
                        "reason": reason
                    })

        # Broadcast state update to Kiosk and Dashboards
        await ws_manager.broadcast_to_kiosk(station_id, {
            "type": "CONNECTOR_STATE_UPDATE",
            "station_id": station_id,
            "connector_id": connector_id,
            "state": state,
            "relay_state": relay_state,
            "reason": reason,
            "uptime_millis": uptime_millis,
            "timestamp": time.time()
        })
        await ws_manager.broadcast_to_all({
            "type": "STATION_CONNECTOR_STATE",
            "station_id": station_id,
            "connector_id": connector_id,
            "state": state,
            "relay_state": relay_state,
            "reason": reason
        })

    async def _handle_telemetry(self, station_id: str, connector_id: str, payload: dict):
        """Processes evsarthi/{station}/{connector}/telemetry."""
        if not isinstance(payload, dict):
            logger.warning(f"[MQTT] _handle_telemetry received non-dict payload: {type(payload).__name__}")
            return

        voltage_v = _safe_float(payload.get("voltage_v"), 0.0)
        current_a = _safe_float(payload.get("current_a"), 0.0)
        power_kw = _safe_float(payload.get("power_kw"), 0.0)
        energy_kwh = _safe_float(payload.get("energy_kwh"), 0.0)

        # Save sanitized timeseries sample to database
        sanitized_payload = {
            **payload,
            "voltage_v": voltage_v,
            "current_a": current_a,
            "power_kw": power_kw,
            "energy_kwh": energy_kwh,
            "voltage_raw": _safe_int(payload.get("voltage_raw"), 0),
            "current_raw": _safe_int(payload.get("current_raw"), 0),
            "millis": _safe_int(payload.get("millis"), 0)
        }
        await database.save_connector_telemetry(station_id, connector_id, sanitized_payload)

        # Update active session if currently charging
        session = await database.get_session(station_id, connector_id)
        current_soc = None
        if session:
            session["kw"] = power_kw
            session["kwh"] = energy_kwh
            session["voltage_v"] = voltage_v
            session["current_a"] = current_a
            session["last_updated"] = time.time()

            # Dynamic SoC estimation (assume 60 kWh pack capacity)
            start_soc = _safe_float(session.get("start_soc"), 20.0)
            added_soc = (energy_kwh / 60.0) * 100.0
            current_soc = min(100.0, round(start_soc + added_soc, 2))
            session["current_soc"] = current_soc

            await database.save_session(session)

            # Broadcast to car dashboard
            user_id = session.get("user_id")
            if user_id:
                await ws_manager.broadcast_to_car(user_id, {
                    "type": "CHARGING_TELEMETRY",
                    "station_id": station_id,
                    "connector_id": connector_id,
                    "session_id": session.get("session_id"),
                    "current_soc": current_soc,
                    "kw": power_kw,
                    "kwh": energy_kwh,
                    "voltage_v": voltage_v,
                    "current_a": current_a,
                    "telemetry": payload
                })

        # Broadcast to kiosk simulator
        await ws_manager.broadcast_to_kiosk(station_id, {
            "type": "METER_VALUES",
            "station_id": station_id,
            "connector_id": connector_id,
            "kw": power_kw,
            "kwh": energy_kwh,
            "voltage_v": voltage_v,
            "current_a": current_a,
            "soc": current_soc,
            "telemetry": payload
        })

    async def _handle_auth(self, station_id: str, connector_id: str, payload: dict):
        """Processes evsarthi/{station}/{connector}/auth and publishes /cmd decision."""
        if not isinstance(payload, dict):
            logger.warning(f"[MQTT] _handle_auth received non-dict payload: {type(payload).__name__}")
            return

        auth_type = str(payload.get("auth_type", "PIN")).upper()
        cred = payload.get("credential", {})
        if isinstance(cred, dict):
            pin_val = str(cred.get("pin") or "").strip()
            rfid_val = str(cred.get("rfid_uid") or "").strip().upper().replace(":", "").replace(" ", "")
        else:
            cred_str = str(cred).strip() if cred is not None else ""
            pin_val = cred_str if auth_type == "PIN" else ""
            rfid_val = cred_str.upper().replace(":", "").replace(" ", "") if auth_type == "RFID" else ""

        now = time.time()
        res = await database.get_reservation(station_id, connector_id)

        # Handle expired reservation check
        if res and res.get("expires_at", 0) < now:
            await database.delete_reservation(station_id, connector_id)
            await database.save_booking_history({
                **res,
                "outcome": "EXPIRED_NO_SHOW",
                "total_kwh": 0,
                "amount_charged_rs": 0
            })
            await ws_manager.broadcast_to_kiosk(station_id, {
                "type": "ACCESS_DENIED",
                "station_id": station_id,
                "connector_id": connector_id,
                "reason": "Reservation expired"
            })
            await self.publish_cmd(
                station_id=station_id,
                connector_id=connector_id,
                action="AUTH_DECISION",
                status="DENIED",
                status_code=401,
                auth_window_duration_ms=0,
                relay_action="DE_ENERGIZE"
            )
            return

        is_authorized = False
        user_id = None
        user_name = "EV Driver"
        vehicle_soc = 20.0

        # Tier 1: Check active reservation
        if res and res.get("expires_at", 0) >= now:
            if auth_type == "PIN" and pin_val and pin_val == str(res.get("pin", "")).strip():
                is_authorized = True
                user_id = res.get("user_id")
                user_name = res.get("user_name", "Hyundai Driver")
                vehicle_soc = res.get("vehicle_soc", 20.0)
            elif auth_type == "RFID" and rfid_val:
                res_rfid = str(res.get("rfid_uid") or "").strip().upper().replace(":", "").replace(" ", "")
                if res_rfid and rfid_val == res_rfid:
                    is_authorized = True
                    user_id = res.get("user_id")
                    user_name = res.get("user_name", "Hyundai Driver")
                    vehicle_soc = res.get("vehicle_soc", 20.0)
                else:
                    card = await database.get_rfid_card(rfid_val)
                    if card and card.get("is_active", 0) == 1 and (not card.get("expires_at") or card.get("expires_at") >= now):
                        if card.get("user_id") == res.get("user_id"):
                            is_authorized = True
                            user_id = res.get("user_id")
                            user_name = card.get("user_name", res.get("user_name", "Hyundai Driver"))
                            vehicle_soc = res.get("vehicle_soc", 20.0)

        # Tier 2: Check fleet / VIP whitelist for walk-up charging (only if not reserved for another user)
        if not is_authorized and auth_type == "RFID" and rfid_val:
            if not res or res.get("expires_at", 0) < now:
                card = await database.get_rfid_card(rfid_val)
                if card and card.get("is_active", 0) == 1 and (not card.get("expires_at") or card.get("expires_at") >= now):
                    is_authorized = True
                    user_id = card.get("user_id", "FLEET_DRIVER")
                    user_name = card.get("user_name", "Fleet Driver")
                    vehicle_soc = 20.0

        if is_authorized:
            status = "ACCEPTED"
            status_code = 200
            auth_window_duration_ms = 30000
            relay_action = "ENERGIZE"
            session_id = f"CHG_{station_id}_{connector_id}_{int(now)}"

            # Create active charging session
            session_data = {
                "station_id": station_id,
                "connector_id": connector_id,
                "session_id": session_id,
                "user_id": user_id or "fleet_user",
                "start_soc": vehicle_soc,
                "current_soc": vehicle_soc,
                "start_time": now,
                "kw": 0.0,
                "kwh": 0.0,
                "voltage_v": 0.0,
                "current_a": 0.0,
                "last_updated": now
            }
            await database.save_session(session_data)

            # Consume reservation if matched
            if res:
                await database.delete_reservation(station_id, connector_id)

            await ws_manager.broadcast_to_kiosk(station_id, {
                "type": "AUTH_ACCEPTED",
                "station_id": station_id,
                "connector_id": connector_id,
                "session_id": session_id,
                "status": "ACCEPTED",
                "auth_type": auth_type,
                "auth_window_ms": 8000,
                "relay_action": "ENERGIZE"
            })
            if user_id:
                await ws_manager.broadcast_to_car(user_id, {
                    "type": "CHARGING_STARTED",
                    "station_id": station_id,
                    "connector_id": connector_id,
                    "session_id": session_id,
                    "start_time": now
                })
        else:
            status = "DENIED"
            status_code = 401
            auth_window_duration_ms = 0
            relay_action = "DE_ENERGIZE"
            reason = "INVALID_PIN" if auth_type == "PIN" else "UNAUTHORIZED_RFID"

            await ws_manager.broadcast_to_kiosk(station_id, {
                "type": "ACCESS_DENIED",
                "station_id": station_id,
                "connector_id": connector_id,
                "status": "DENIED",
                "auth_type": auth_type,
                "reason": reason
            })

        # Publish decision back to firmware on /cmd
        await self.publish_cmd(
            station_id=station_id,
            connector_id=connector_id,
            action="AUTH_DECISION",
            status=status,
            status_code=status_code,
            auth_window_duration_ms=auth_window_duration_ms,
            relay_action=relay_action
        )

    # ── Outbound Publishing API ─────────────────────────────────────────────

    async def publish(self, topic: str, payload: Union[dict, str], qos: int = 1, retain: bool = False) -> bool:
        """Publishes a payload to an MQTT topic."""
        payload_str = json.dumps(payload) if isinstance(payload, dict) else str(payload)
        if self.client and self._connected:
            info = self.client.publish(topic, payload_str, qos=qos, retain=retain)
            res_ok = (info.rc == mqtt.MQTT_ERR_SUCCESS)
        else:
            logger.debug(f"[MQTT (Simulated)] Publish to {topic}: {payload_str}")
            res_ok = True

        # Parse payload for custom listener notifications
        parsed_payload = payload
        if isinstance(payload, str):
            try:
                parsed_payload = json.loads(payload)
            except Exception:
                pass

        # Notify any custom listeners registered by tests or M3 CSMS bridge
        for pattern, listeners in list(self._custom_listeners.items()):
            if self._topic_matches(pattern, topic):
                for listener in listeners:
                    try:
                        res = listener(topic, parsed_payload)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as e:
                        logger.error(f"[MQTT] Error in custom listener for topic {topic}: {e}")

        return res_ok

    async def publish_reserve(
        self,
        station_id: str,
        connector_id: str,
        action: str,
        reservation_id: str,
        pin: str,
        duration_seconds: int = 60,
        expires_at: Optional[int] = None,
        rfid_uid: str = ""
    ) -> bool:
        """Publishes reservation command to evsarthi/{station}/{connector}/reserve."""
        topic = f"evsarthi/{station_id}/{connector_id}/reserve"
        payload = {
            "action": action,
            "reservation_id": reservation_id,
            "pin": pin,
            "duration_seconds": duration_seconds,
            "expires_at": expires_at or int(time.time() + duration_seconds),
            "rfid_uid": rfid_uid or ""
        }
        return await self.publish(topic, payload, qos=1, retain=False)

    async def publish_cmd(
        self,
        station_id: str,
        connector_id: str,
        action: str = "AUTH_DECISION",
        status: str = "ACCEPTED",
        status_code: int = 200,
        auth_window_duration_ms: int = 30000,
        relay_action: str = "ENERGIZE"
    ) -> bool:
        """Publishes control decision to evsarthi/{station}/{connector}/cmd."""
        topic = f"evsarthi/{station_id}/{connector_id}/cmd"
        payload = {
            "action": action,
            "status": status,
            "status_code": status_code,
            "auth_window_duration_ms": auth_window_duration_ms,
            "relay_action": relay_action
        }
        return await self.publish(topic, payload, qos=1, retain=False)

    def add_message_listener(self, pattern: str, callback: Callable):
        """Registers external event listener for tests or M3 CSMS integration."""
        self._custom_listeners.setdefault(pattern, []).append(callback)

    def _topic_matches(self, pattern: str, topic: str) -> bool:
        """Evaluates MQTT wildcard pattern matching (+ and #)."""
        p_parts = pattern.split('/')
        t_parts = topic.split('/')
        if '#' in p_parts:
            idx = p_parts.index('#')
            return p_parts[:idx] == t_parts[:idx]
        if len(p_parts) != len(t_parts):
            return False
        return all(p == '+' or p == t for p, t in zip(p_parts, t_parts))


mqtt_service = MQTTService()
