"""The home page."""
from flask import Blueprint, render_template

from ..auth import login_required

bp = Blueprint("main", __name__)


@bp.get("/")
@login_required
def home():
    return render_template("home.html")
