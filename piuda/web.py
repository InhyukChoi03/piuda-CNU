from pathlib import Path

from flask import Blueprint, abort, current_app, make_response, render_template, send_file, send_from_directory

from .auth import is_private_request
from .demo import DEMO_GROUPS, scenario_catalog


web = Blueprint("web", __name__)


@web.get("/")
def user_home():
    return render_template("user.html")


@web.get("/caregiver")
def caregiver_home():
    return render_template("caregiver.html")


@web.get("/install")
def install_guide():
    return render_template("install.html")


@web.get("/piuda-ca.crt")
def local_call_certificate():
    certificate = Path(current_app.config["DATA_DIR"]) / "tls" / "piuda-ca.crt"
    if not certificate.is_file():
        abort(404)
    return send_file(
        certificate,
        mimetype="application/x-x509-ca-cert",
        as_attachment=True,
        download_name="piuda-local-ca.crt",
    )


@web.get("/demo")
def demo_console():
    if not current_app.config.get("DEMO_MODE"):
        abort(404)
    if not is_private_request():
        abort(403)
    scenarios = scenario_catalog()
    groups = [
        {"key": key, "title": title, "items": [item for item in scenarios if item["group"] == key]}
        for key, title in DEMO_GROUPS
    ]
    return render_template("demo.html", groups=groups)


@web.get("/manifest.webmanifest")
def manifest():
    response = make_response(current_app.send_static_file("manifest.webmanifest"))
    response.headers["Content-Type"] = "application/manifest+json"
    response.headers["Cache-Control"] = "no-cache"
    return response


@web.get("/caregiver-manifest.webmanifest")
def caregiver_manifest():
    response = make_response(current_app.send_static_file("caregiver-manifest.webmanifest"))
    response.headers["Content-Type"] = "application/manifest+json"
    response.headers["Cache-Control"] = "no-cache"
    return response


@web.get("/service-worker.js")
def service_worker():
    response = make_response(send_from_directory(current_app.static_folder, "service-worker.js", mimetype="application/javascript"))
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response
