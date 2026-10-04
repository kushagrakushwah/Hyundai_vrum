# 🚀 Deploying Hyundai SmartReserve to Render

## Option 1: Auto-Deploy from GitHub (Recommended)
1. Push this repo to GitHub
2. Go to https://render.com → New → Web Service
3. Connect your GitHub repo: `kushagrakushwah/Hyundai_vrum`
4. Set these settings:
   - **Root Directory:** (leave blank)
   - **Build Command:** `pip install -r smartreserve/requirements.txt`
   - **Start Command:** `cd smartreserve/smartreserve_backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Environment:** Python 3
5. Add Environment Variables:
   - `RAZORPAY_KEY_ID` = your real key (or leave placeholder for demo)
   - `RAZORPAY_KEY_SECRET` = your real secret
   - `OCM_API_KEY` = 1f545914-8daa-4fa6-9d7b-4a6819f2f7cc
6. Click **Deploy**!

## Option 2: Docker (Local Testing)
```bash
docker-compose up --build
```
Then open: http://localhost:8000/static/car_dashboard/index.html

## Accessing the App
- **API Docs:** https://your-app.onrender.com/docs
- **Car Dashboard:** https://your-app.onrender.com/static/car_dashboard/index.html
- **Health Check:** https://your-app.onrender.com/health

## Environment Variables
| Variable | Required | Description |
|---|---|---|
| `PORT` | Yes (auto-set by Render) | Server port |
| `RAZORPAY_KEY_ID` | Optional | Razorpay API key (demo works without) |
| `RAZORPAY_KEY_SECRET` | Optional | Razorpay secret |
| `OCM_API_KEY` | Optional | Open Charge Map API key |
