import os
import time
import aiosqlite
from typing import Optional, List, Dict, Any, Union

DB_PATH = os.path.join(os.path.dirname(__file__), 'smartreserve.db')

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Check if reservations table requires composite key migration
        async with db.execute("PRAGMA table_info(reservations)") as cursor:
            rows = await cursor.fetchall()
            cols = [r[1] for r in rows]
            if cols and "connector_id" not in cols:
                # Migrate reservations table to composite primary key (station_id, connector_id)
                await db.execute('''
                    CREATE TABLE reservations_v2 (
                        station_id TEXT NOT NULL,
                        connector_id TEXT NOT NULL DEFAULT '1',
                        reservation_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        user_name TEXT,
                        pin TEXT NOT NULL,
                        rfid_uid TEXT DEFAULT NULL,
                        duration_minutes INTEGER DEFAULT 30,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        vehicle_soc REAL DEFAULT 20.0,
                        payment_order_id TEXT DEFAULT NULL,
                        payment_status TEXT DEFAULT 'PENDING',
                        PRIMARY KEY (station_id, connector_id)
                    )
                ''')
                await db.execute('''
                    INSERT INTO reservations_v2 (
                        station_id, connector_id, reservation_id, user_id, user_name,
                        pin, duration_minutes, created_at, expires_at, vehicle_soc,
                        payment_order_id, payment_status
                    )
                    SELECT station_id, '1', reservation_id, user_id, user_name,
                           pin, duration_minutes, created_at, expires_at, vehicle_soc,
                           payment_order_id, payment_status
                    FROM reservations
                ''')
                await db.execute('DROP TABLE reservations')
                await db.execute('ALTER TABLE reservations_v2 RENAME TO reservations')

        # Check if charging_sessions table requires composite key migration
        async with db.execute("PRAGMA table_info(charging_sessions)") as cursor:
            rows = await cursor.fetchall()
            cols = [r[1] for r in rows]
            if cols and "connector_id" not in cols:
                await db.execute('''
                    CREATE TABLE charging_sessions_v2 (
                        station_id TEXT NOT NULL,
                        connector_id TEXT NOT NULL DEFAULT '1',
                        session_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        start_soc REAL DEFAULT 20.0,
                        current_soc REAL DEFAULT 20.0,
                        start_time REAL NOT NULL,
                        kw REAL DEFAULT 0.0,
                        kwh REAL DEFAULT 0.0,
                        voltage_v REAL DEFAULT 0.0,
                        current_a REAL DEFAULT 0.0,
                        last_updated REAL,
                        PRIMARY KEY (station_id, connector_id)
                    )
                ''')
                await db.execute('''
                    INSERT INTO charging_sessions_v2 (
                        station_id, connector_id, session_id, user_id,
                        start_soc, current_soc, start_time, kw, kwh
                    )
                    SELECT station_id, '1', session_id, user_id,
                           start_soc, current_soc, start_time, kw, kwh
                    FROM charging_sessions
                ''')
                await db.execute('DROP TABLE charging_sessions')
                await db.execute('ALTER TABLE charging_sessions_v2 RENAME TO charging_sessions')

        # Check if bookings_history table requires connector_id column
        async with db.execute("PRAGMA table_info(bookings_history)") as cursor:
            rows = await cursor.fetchall()
            cols = [r[1] for r in rows]
            if cols and "connector_id" not in cols:
                await db.execute("ALTER TABLE bookings_history ADD COLUMN connector_id TEXT DEFAULT '1'")

        # Create tables if not existing
        await db.execute('''
        CREATE TABLE IF NOT EXISTS reservations (
            station_id TEXT NOT NULL,
            connector_id TEXT NOT NULL DEFAULT '1',
            reservation_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT,
            pin TEXT NOT NULL,
            rfid_uid TEXT DEFAULT NULL,
            duration_minutes INTEGER DEFAULT 30,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            vehicle_soc REAL DEFAULT 20.0,
            payment_order_id TEXT DEFAULT NULL,
            payment_status TEXT DEFAULT 'PENDING',
            PRIMARY KEY (station_id, connector_id)
        )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_res_reservation_id ON reservations(reservation_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_res_pin ON reservations(station_id, pin)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_res_rfid ON reservations(station_id, rfid_uid)')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS charging_sessions (
            station_id TEXT NOT NULL,
            connector_id TEXT NOT NULL DEFAULT '1',
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            start_soc REAL DEFAULT 20.0,
            current_soc REAL DEFAULT 20.0,
            start_time REAL NOT NULL,
            kw REAL DEFAULT 0.0,
            kwh REAL DEFAULT 0.0,
            voltage_v REAL DEFAULT 0.0,
            current_a REAL DEFAULT 0.0,
            last_updated REAL,
            PRIMARY KEY (station_id, connector_id)
        )
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS bookings_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id TEXT,
            station_id TEXT NOT NULL,
            connector_id TEXT DEFAULT '1',
            user_id TEXT,
            user_name TEXT,
            created_at REAL,
            expires_at REAL,
            outcome TEXT,
            total_kwh REAL DEFAULT 0,
            amount_charged_rs REAL DEFAULT 0
        )
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS connector_telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id TEXT NOT NULL,
            connector_id TEXT NOT NULL DEFAULT '1',
            voltage_v REAL NOT NULL,
            current_a REAL NOT NULL,
            power_kw REAL NOT NULL,
            energy_kwh REAL NOT NULL,
            voltage_raw INTEGER,
            current_raw INTEGER,
            millis INTEGER,
            recorded_at REAL NOT NULL
        )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_telemetry_station_conn ON connector_telemetry(station_id, connector_id, recorded_at DESC)')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS authorized_rfid_cards (
            card_uid TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            user_name TEXT,
            card_label TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            expires_at REAL DEFAULT NULL
        )
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS connector_status (
            station_id TEXT NOT NULL,
            connector_id TEXT NOT NULL DEFAULT '1',
            state TEXT NOT NULL DEFAULT 'AVAILABLE',
            relay_state TEXT NOT NULL DEFAULT 'DE_ENERGIZED',
            transition_reason TEXT,
            uptime_millis INTEGER,
            last_heartbeat REAL NOT NULL,
            PRIMARY KEY (station_id, connector_id)
        )
        ''')

        # Pre-seed authorized RFID whitelist for testing and fleet operations
        default_cards = [
            ("DEADBEEF", "fleet_driver_001", "Fleet Driver #1", "Hyundai Fleet Tag", 1, time.time(), None),
            ("A1B2C3D4", "hyundai_driver_001", "Kushagra Verma", "Personal Keyfob", 1, time.time(), None),
            ("CAFEFACE", "vip_driver_007", "VIP Driver", "Hyundai VIP Gold", 1, time.time(), None),
            ("0456789A", "judge_eval_001", "Hackathon Judge", "Evaluation Card", 1, time.time(), None),
        ]
        await db.executemany('''
            INSERT OR IGNORE INTO authorized_rfid_cards 
            (card_uid, user_id, user_name, card_label, is_active, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', default_cards)

        await db.commit()

async def save_reservation(
    data_or_station: Union[dict, str],
    connector_id: str = "1",
    reservation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    pin: Optional[str] = None,
    rfid_uid: Optional[str] = None,
    duration_minutes: int = 30,
    created_at: Optional[float] = None,
    expires_at: Optional[float] = None,
    vehicle_soc: float = 20.0,
    payment_order_id: Optional[str] = None,
    payment_status: str = 'PENDING'
):
    if isinstance(data_or_station, dict):
        d = data_or_station
        st_id = d.get('station_id')
        conn_id = str(d.get('connector_id', '1'))
        res_id = d.get('reservation_id')
        u_id = d.get('user_id')
        u_name = d.get('user_name')
        p_pin = str(d.get('pin', ''))
        r_uid = d.get('rfid_uid')
        dur = d.get('duration_minutes', 30)
        c_at = d.get('created_at', time.time())
        e_at = d.get('expires_at', time.time() + 1800)
        soc = d.get('vehicle_soc', 20.0)
        p_order = d.get('payment_order_id')
        p_stat = d.get('payment_status', 'PENDING')
    else:
        st_id = data_or_station
        conn_id = str(connector_id)
        res_id = reservation_id
        u_id = user_id
        u_name = user_name
        p_pin = str(pin or '')
        r_uid = rfid_uid
        dur = duration_minutes
        c_at = created_at or time.time()
        e_at = expires_at or (c_at + dur * 60)
        soc = vehicle_soc
        p_order = payment_order_id
        p_stat = payment_status

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
        INSERT OR REPLACE INTO reservations 
        (station_id, connector_id, reservation_id, user_id, user_name, pin, rfid_uid,
         duration_minutes, created_at, expires_at, vehicle_soc, payment_order_id, payment_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            st_id, conn_id, res_id, u_id, u_name, p_pin, r_uid, dur, c_at, e_at, soc, p_order, p_stat
        ))
        await db.commit()

async def get_reservation(station_id: str, connector_id: Optional[str] = "1") -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if connector_id:
            query = 'SELECT * FROM reservations WHERE station_id = ? AND connector_id = ?'
            params = (station_id, str(connector_id))
        else:
            query = 'SELECT * FROM reservations WHERE station_id = ? ORDER BY expires_at DESC LIMIT 1'
            params = (station_id,)
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def delete_reservation(station_id: str, connector_id: Optional[str] = "1"):
    async with aiosqlite.connect(DB_PATH) as db:
        if connector_id:
            await db.execute('DELETE FROM reservations WHERE station_id = ? AND connector_id = ?', (station_id, str(connector_id)))
        else:
            await db.execute('DELETE FROM reservations WHERE station_id = ?', (station_id,))
        await db.commit()

async def get_all_active_reservations() -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM reservations') as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def save_session(
    data_or_station: Union[dict, str],
    connector_id: str = "1",
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    start_soc: float = 20.0,
    current_soc: float = 20.0,
    start_time: Optional[float] = None,
    kw: float = 0.0,
    kwh: float = 0.0,
    voltage_v: float = 0.0,
    current_a: float = 0.0,
    last_updated: Optional[float] = None
):
    if isinstance(data_or_station, dict):
        d = data_or_station
        st_id = d.get('station_id')
        conn_id = str(d.get('connector_id', '1'))
        s_id = d.get('session_id')
        u_id = d.get('user_id')
        s_soc = d.get('start_soc', 20.0)
        c_soc = d.get('current_soc', 20.0)
        s_time = d.get('start_time') or time.time()
        k_w = d.get('kw', 0.0)
        k_wh = d.get('kwh', 0.0)
        v_v = d.get('voltage_v', 0.0)
        c_a = d.get('current_a', 0.0)
        l_up = d.get('last_updated', time.time())
    else:
        st_id = data_or_station
        conn_id = str(connector_id)
        s_id = session_id
        u_id = user_id
        s_soc = start_soc
        c_soc = current_soc
        s_time = start_time or time.time()
        k_w = kw
        k_wh = kwh
        v_v = voltage_v
        c_a = current_a
        l_up = last_updated or time.time()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
        INSERT OR REPLACE INTO charging_sessions 
        (station_id, connector_id, session_id, user_id, start_soc, current_soc,
         start_time, kw, kwh, voltage_v, current_a, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            st_id, conn_id, s_id, u_id, s_soc, c_soc, s_time, k_w, k_wh, v_v, c_a, l_up
        ))
        await db.commit()

async def get_session(station_id: str, connector_id: Optional[str] = "1") -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if connector_id:
            query = 'SELECT * FROM charging_sessions WHERE station_id = ? AND connector_id = ?'
            params = (station_id, str(connector_id))
        else:
            query = 'SELECT * FROM charging_sessions WHERE station_id = ? LIMIT 1'
            params = (station_id,)
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def delete_session(station_id: str, connector_id: Optional[str] = "1"):
    async with aiosqlite.connect(DB_PATH) as db:
        if connector_id:
            await db.execute('DELETE FROM charging_sessions WHERE station_id = ? AND connector_id = ?', (station_id, str(connector_id)))
        else:
            await db.execute('DELETE FROM charging_sessions WHERE station_id = ?', (station_id,))
        await db.commit()

async def get_all_sessions() -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM charging_sessions') as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def save_booking_history(data: dict):
    connector_id = str(data.get('connector_id', '1'))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
        INSERT INTO bookings_history 
        (reservation_id, station_id, connector_id, user_id, user_name, created_at, expires_at, outcome, total_kwh, amount_charged_rs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('reservation_id'),
            data.get('station_id'),
            connector_id,
            data.get('user_id'),
            data.get('user_name'),
            data.get('created_at'),
            data.get('expires_at'),
            data.get('outcome'),
            data.get('total_kwh', 0),
            data.get('amount_charged_rs', 0)
        ))
        await db.commit()

async def get_history(limit: int = 50) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM bookings_history ORDER BY id DESC LIMIT ?', (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def save_connector_telemetry(station_id_or_data: Union[str, dict], connector_id: str = "1", payload: Optional[dict] = None):
    if isinstance(station_id_or_data, dict):
        d = station_id_or_data
        st_id = d.get('station_id')
        conn_id = str(d.get('connector_id', '1'))
        p = d
    else:
        st_id = station_id_or_data
        conn_id = str(connector_id)
        p = payload or {}

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
        INSERT INTO connector_telemetry 
        (station_id, connector_id, voltage_v, current_a, power_kw, energy_kwh,
         voltage_raw, current_raw, millis, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            st_id,
            conn_id,
            p.get('voltage_v', 0.0),
            p.get('current_a', 0.0),
            p.get('power_kw', 0.0),
            p.get('energy_kwh', 0.0),
            p.get('voltage_raw', 0),
            p.get('current_raw', 0),
            p.get('millis', 0),
            p.get('recorded_at', time.time())
        ))
        await db.commit()

async def get_latest_telemetry(station_id: str, connector_id: str = "1") -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT * FROM connector_telemetry WHERE station_id = ? AND connector_id = ? ORDER BY recorded_at DESC, id DESC LIMIT 1',
            (station_id, str(connector_id))
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def is_rfid_authorized(card_uid: str) -> bool:
    normalized = card_uid.strip().upper().replace(":", "").replace(" ", "")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT is_active, expires_at FROM authorized_rfid_cards WHERE card_uid = ?',
            (normalized,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return False
            is_active, expires_at = row[0], row[1]
            if is_active != 1:
                return False
            if expires_at and expires_at < time.time():
                return False
            return True

async def get_rfid_card(card_uid: str) -> Optional[dict]:
    normalized = card_uid.strip().upper().replace(":", "").replace(" ", "")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM authorized_rfid_cards WHERE card_uid = ?', (normalized,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

# Alias for get_rfid_card for compatibility
get_authorized_rfid = get_rfid_card

async def add_authorized_rfid(
    card_uid: str,
    user_id: str,
    user_name: str,
    card_label: str = "Fleet Card",
    is_active: int = 1,
    expires_at: Optional[float] = None
):
    normalized = card_uid.strip().upper().replace(":", "").replace(" ", "")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT OR REPLACE INTO authorized_rfid_cards 
            (card_uid, user_id, user_name, card_label, is_active, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (normalized, user_id, user_name, card_label, is_active, time.time(), expires_at))
        await db.commit()

async def save_connector_status(
    station_id_or_data: Union[str, dict],
    connector_id: str = "1",
    payload: Optional[dict] = None
):
    if isinstance(station_id_or_data, dict):
        d = station_id_or_data
        st_id = d.get('station_id')
        conn_id = str(d.get('connector_id', '1'))
        p = d
    else:
        st_id = station_id_or_data
        conn_id = str(connector_id)
        p = payload or {}

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT OR REPLACE INTO connector_status
            (station_id, connector_id, state, relay_state, transition_reason, uptime_millis, last_heartbeat)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            st_id,
            conn_id,
            p.get('state', 'AVAILABLE'),
            p.get('relay_state', 'DE_ENERGIZED'),
            p.get('transition_reason', ''),
            p.get('uptime_millis', 0),
            p.get('last_heartbeat', time.time())
        ))
        await db.commit()

async def get_connector_status(station_id: str, connector_id: str = "1") -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT * FROM connector_status WHERE station_id = ? AND connector_id = ?',
            (station_id, str(connector_id))
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_all_connector_statuses() -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM connector_status') as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
