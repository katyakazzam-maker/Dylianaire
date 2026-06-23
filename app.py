"""
app.py
Flask web app: serves the candidate dashboard and a manual refresh trigger.
"""

import os
from flask import Flask, render_template, redirect, url_for, request
from discovery import run_discovery, load_candidates

app = Flask(__name__)


@app.route("/")
def dashboard():
    candidates = load_candidates()
    genre_filter = request.args.get("genre", "").lower().strip()
    if genre_filter:
        candidates = [c for c in candidates if genre_filter in c["genres"].lower()]
    return render_template("dashboard.html", candidates=candidates, genre_filter=genre_filter)


@app.route("/refresh", methods=["POST"])
def refresh():
    run_discovery()
    return redirect(url_for("dashboard"))


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
