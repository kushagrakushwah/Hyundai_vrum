"""
Generate demo station data CSV — mirrors the real nodes_master.csv format.
Run this to pre-generate nodes_master.csv before starting the server.
The backend will auto-generate data in memory if this file doesn't exist.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'smartreserve_backend'))

from stations_db import StationsDB

if __name__ == '__main__':
    db = StationsDB()
    out = os.path.join(os.path.dirname(__file__), 'nodes_master.csv')
    db.save_to_csv(out)
    print(f"✅ Generated {len(db.stations)} stations → {out}")
