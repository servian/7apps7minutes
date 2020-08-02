import json
import os
import random
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_cors import cross_origin
from pyfiglet import Figlet

load_dotenv(dotenv_path="theme.env", verbose=True)

VERSION = os.getenv("VERSION") or os.getenv("GAE_VERSION")
FONT = os.getenv("FONT")
ASCII_FONT = os.getenv("ASCII_FONT")
GRADIENT = os.getenv("GRADIENT")


@dataclass
class AppTheme:
    font: Optional[str] = None
    ascii_font: Optional[str] = None
    gradient: Optional[str] = None
    colors: List[str] = field(init=False)

    def __post_init__(self):
        with open("theme.json", "r") as f:
            theme = json.load(f)
        self.gradients = {i["name"]: i["colors"] for i in theme["gradients"]}
        if not self.gradient:
            self.gradient = random.choice(list(self.gradients.keys()))
        if not self.font:
            self.font = random.choice(theme["fonts"])
        if not self.ascii_font:
            self.ascii_font = random.choice(theme["ascii_fonts"])
        self.colors = self.gradients[self.gradient]


@dataclass
class App:
    title: str
    version: Optional[str]
    theme: AppTheme

    @property
    def header(self) -> str:
        return Figlet(font=self.theme.ascii_font).renderText("7Apps")


# Infer runtime environment from available environment variables + set title
if os.getenv("GAE_ENV") == "standard":
    title = "App Engine: Standard"
elif os.getenv("GAE_SERVICE") == "flexible":
    title = "App Engine: Flexible"
elif "FUNCTION_TARGET" in os.environ:
    title = "Cloud Function"
elif "K_SERVICE" in os.environ and any("ANTHOS" in v for v in os.environ):
    title = "Cloud Run: Anthos"
elif "K_SERVICE" in os.environ:
    title = "Cloud Run: Managed"
elif any(v.startswith("GKE_APP") for v in os.environ):
    title = "Kubernetes Engine"
elif "GCE_APP" in os.environ:
    title = "Compute Engine"
else:
    title = "7-Apps Demo"

app = Flask("7apps")
app_data = App(
    title=title,
    version=VERSION,
    theme=AppTheme(font=FONT, ascii_font=ASCII_FONT, gradient=GRADIENT),
)


@app.route("/")
@cross_origin(send_wildcard=True)
def main(*args, **kwargs):
    if request.headers.get("Accept") == "application/json":
        return jsonify(asdict(app_data))

    return render_template("index.html", app=app_data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
