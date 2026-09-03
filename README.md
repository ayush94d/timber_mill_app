# Awadh Saw Mill — Estimate, Challan & Payroll

A small business app: build multi-item timber estimates, confirm orders into
numbered delivery challans, and track employee attendance & monthly payroll.

Backend: Python (Flask) + SQLite.
Frontend: plain HTML/CSS/JavaScript in `static/`, calling the Flask API.

## Run it locally

```
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000 in your browser. On your phone, connect to the
same Wi-Fi as your computer and open `http://<your-computer's-local-IP>:5000`.

## Deploy for free (so it works from any device, anywhere)

**Render.com** (recommended — free tier, no credit card):

1. Create a free account at render.com and a free account at github.com if
   you don't already have one.
2. Upload this whole folder to a new GitHub repository (drag-and-drop works
   on github.com — click "Add file" → "Upload files").
3. In Render, click **New → Web Service**, connect your GitHub repo.
4. Settings:
   - Runtime: Python 3
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
   - Instance type: Free
5. Click **Create Web Service**. Render gives you a live URL like
   `https://awadh-saw-mill.onrender.com` — open that on your phone.

**Note on the free tier:** Render's free web services spin down after a
period of inactivity and take ~30-50 seconds to wake up on the next visit.
That's fine for a small shop tool — just expect the first load of the day
to take a little longer.

**Note on the database:** SQLite (`awadh.db`) lives on Render's disk, which
is reset on every deploy on the free tier. For a shop that wants data to
truly persist long-term, the next step up is Render's free PostgreSQL
add-on — ask if you'd like that wired in.

## Project structure

```
app.py                 Flask backend + API + SQLite schema
requirements.txt       Python dependencies
Procfile                Tells the host how to start the app
static/
  index.html           App shell (Estimate tab + Payroll tab)
  css/style.css         Green/white theme, mobile-friendly
  js/app.js             All frontend logic, talks to /api/*
```
