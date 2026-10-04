FROM python:3.11-slim

WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PORT=8000 \
    PYTHONPATH=/app/smartreserve_backend

# Install dependencies
COPY smartreserve/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend
COPY smartreserve/smartreserve_backend/ ./smartreserve_backend/

# Copy frontend to be served by FastAPI static mount
COPY smartreserve/smartreserve_frontend/ ./smartreserve_frontend/

# Copy processed data (BEE stations CSV)
COPY EVCS_Demand_Forecasting/processed/ ./EVCS_Demand_Forecasting/processed/

# Ensure dataset is accessible via both relative search paths in stations_db.py
RUN mkdir -p /EVCS_Demand_Forecasting && \
    ln -s /app/EVCS_Demand_Forecasting/processed /EVCS_Demand_Forecasting/processed && \
    mkdir -p /app/demo_data && \
    cp /app/EVCS_Demand_Forecasting/processed/nodes_master.csv /app/demo_data/nodes_master.csv

# Expose port
EXPOSE 8000

# Start server
CMD ["sh", "-c", "cd smartreserve_backend && uvicorn main:app --host 0.0.0.0 --port $PORT"]
