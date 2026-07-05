# Policy Register — LIC Agent Web App

A simple, private web app for storing policyholder details, tracking
policies, and viewing month-wise stats. Built with Python (Flask) and
SQLite — no external database or paid service needed.

## Features
- Login screen (single agent account, password stored securely hashed)
- Add / edit / delete policyholders with full details:
  name, DOB, gender, phone, email, address, nominee, **policy name**,
  **policy number**, policy type, sum assured, premium amount &
  frequency, start/maturity dates, status, notes
- Dashboard with:
  - Pie chart of policies by type
  - Bar chart of new policies by month (your "month-wise policy strategy" view)
  - Quick stats: total policyholders, active policies, total premium, total sum assured
- Search policyholders by name / policy name / policy number
- **Download all data as CSV** any time (button in the sidebar)
- Change your password from Settings

## Setup (one-time)

You need Python 3.9+ installed. Then, in this folder, run:

```bash
pip install -r requirements.txt
python app.py
```

Open your browser to **http://127.0.0.1:5000**

### First login
```
Username: admin
Password: changeme123
```
**Change this password immediately** — go to "settings" (bottom left,
next to your username) once you're logged in.

## Where is my data stored?

Everything is saved in a single file: `lic_manager.db` (created
automatically next to `app.py`, in the same folder). To back up your
data, just copy that file somewhere safe — or use the "Export CSV"
button any time to get a spreadsheet copy of every record.

## Running it so it's always available on your computer

For daily use, just double-click to run `python app.py` (or open a
terminal in this folder and run it) whenever your mum wants to use it,
then open http://127.0.0.1:5000 in the browser. Keep the terminal
window open while using the site — closing it stops the server.

## Putting this on the internet (optional, later)

This currently runs only on your own computer ("localhost"). If you
later want your mum to access it from her phone or from anywhere, it
would need to be deployed to a hosting service (e.g. PythonAnywhere,
Render, Railway) — happy to help with that step whenever you're ready.
A couple of things worth doing first if you go that route:
- Set a strong, random `SECRET_KEY` environment variable
- Use HTTPS
- Consider adding rate-limiting to the login page

## Project structure

```
lic_manager/
├── app.py                  # Flask app (routes, database logic)
├── requirements.txt
├── lic_manager.db           # created on first run — your data lives here
├── templates/               # HTML pages
└── static/style.css         # styling
```
