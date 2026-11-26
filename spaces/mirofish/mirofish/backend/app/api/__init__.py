"""
API Routes Module
"""

from flask import Blueprint

graph_bp = Blueprint('graph', __name__)
simulation_bp = Blueprint('simulation', __name__)
report_bp = Blueprint('report', __name__)
bubble_bp = Blueprint('bubble', __name__)  # Bubble-Bewertung (VibeMind)

from . import graph  # noqa: E402, F401
from . import simulation  # noqa: E402, F401
from . import report  # noqa: E402, F401
from . import bubble_bewertung  # noqa: E402, F401

