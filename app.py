from flask import Flask, jsonify, render_template, request

import db
import monitor
from keywords import STATUS_TEXT

app = Flask(__name__)
db.init()


def _run(domain):
    try:
        return monitor.check_domain(domain), None
    except ValueError as e:
        return None, str(e)
    except Exception:  # never show a stack trace to a reviewer
        app.logger.exception("check failed")
        return None, "Something went wrong while checking that domain. Please try again."


@app.get("/")
def index():
    return render_template("index.html", result=None, error=None, runs=db.recent_runs(8), kw_text=STATUS_TEXT)


@app.post("/check")
def check():
    domain = request.form.get("domain", "")
    result, error = _run(domain)
    return render_template("index.html", result=result, error=error, runs=db.recent_runs(8),
                           domain=domain, kw_text=STATUS_TEXT)


@app.post("/api/check")
def api_check():
    domain = (request.get_json(silent=True) or {}).get("domain", "")
    result, error = _run(domain)
    return (jsonify({"error": error}), 400) if error else jsonify(result)


@app.post("/reset")
def reset():
    """Demo helper: forget a domain so you can show a fresh first run."""
    domain = request.form.get("domain", "")
    try:
        db.reset_domain(monitor.normalize_domain(domain)[1])
    except ValueError:
        pass
    return render_template("index.html", result=None, error=None, runs=db.recent_runs(8),
                           domain=domain, kw_text=STATUS_TEXT, notice=f"Cleared saved data for {domain}.")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
