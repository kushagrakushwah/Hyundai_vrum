import os
import time
import aiosqlite

DB_PATH = os.path.join(os.path.dirname(__file__), 'smartreserve.db')

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
        CREATE TABLE IF NOT EXISTS reservations (
            station_id TEXT PRIMARY KEY,
            reservation_id TEXT,
            user_id TEXT,
            user_name TEXT,
            pin TEXT,
            duration_minutes INTEGER,
            created_at REAL,
            expires_at REAL,
            vehicle_soc REAL,
            payment_order_id TEXT DEFAULT NULL,
            payment_status TEXT DEFAULT 'PENDING'
        )
        ''')
        
        await db.execute('''
        CREATE TABLE IF NOT EXISTS charging_sessions (
            station_id TEXT PRIMARY KEY,
            session_id TEXT,
            user_id TEXT,
            start_soc REAL,
            current_soc REAL,
            start_time REAL,
            kw REAL,
            kwh REAL
        )
        ''')
        
        await db.execute('''
        CREATE TABLE IF NOT EXISTS bookings_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id TEXT,
            station_id TEXT,
            user_id TEXT,
            user_name TEXT,
            created_at REAL,
            expires_at REAL,
            outcome TEXT,
            total_kwh REAL DEFAULT 0,
            amount_charged_rs REAL DEFAULT 0
        )
        ''')
        await db.commit()

async def save_reservation(data: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
        INSERT OR REPLACE INTO reservations 
        (station_id, reservation_id, user_id, user_name, pin, duration_minutes, created_at, expires_at, vehicle_soc, payment_order_id, payment_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('station_id'),
            data.get('reservation_id'),
            data.get('user_id'),
            data.get('user_name'),
            data.get('pin'),
            data.get('duration_minutes'),
            data.get('created_at'),
            data.get('expires_at'),
            data.get('vehicle_soc'),
            data.get('payment_order_id'),
            data.get('payment_status', 'PENDING')
        ))
        await db.commit()

async def get_reservation(station_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM reservations WHERE station_id = ?', (station_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

async def delete_reservation(station_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM reservations WHERE station_id = ?', (station_id,))
        await db.commit()

async def get_all_active_reservations() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        current_time = time.time()
        async with db.execute('SELECT * FROM reservations WHERE expires_at > ?', (current_time,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def save_session(data: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
        INSERT OR REPLACE INTO charging_sessions 
        (station_id, session_id, user_id, start_soc, current_soc, start_time, kw, kwh)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('station_id'),
            data.get('session_id'),
            data.get('user_id'),
            data.get('start_soc'),
            data.get('current_soc'),
            data.get('start_time'),
            data.get('kw'),
            data.get('kwh')
        ))
        await db.commit()

async def get_session(station_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM charging_sessions WHERE station_id = ?', (station_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

async def delete_session(station_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM charging_sessions WHERE station_id = ?', (station_id,))
        await db.commit()

async def get_all_sessions() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM charging_sessions') as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def save_booking_history(data: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
        INSERT INTO bookings_history 
        (reservation_id, station_id, user_id, user_name, created_at, expires_at, outcome, total_kwh, amount_charged_rs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('reservation_id'),
            data.get('station_id'),
            data.get('user_id'),
            data.get('user_name'),
            data.get('created_at'),
            data.get('expires_at'),
            data.get('outcome'),
            data.get('total_kwh', 0),
            data.get('amount_charged_rs', 0)
        ))
        await db.commit()

async def get_history(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM bookings_history ORDER BY id DESC LIMIT ?', (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
