"""
TIM Tunisia — Full-Platform Demo Simulator
===========================================
A self-contained simulator that populates the LLM Dashboard with realistic
data and runs live traffic for demonstrating every platform feature.

Modes:
    python -m simulator --mode seed       # Populate 60 days of historical data
    python -m simulator --mode live       # Continuous live traffic via gateway API
    python -m simulator --mode demo       # Guided 7-act demo for jury presentation
    python -m simulator --mode seed+live  # Seed then go live
    python -m simulator --mode reset      # Wipe all simulator data
    python -m simulator --mode verify     # Verify data integrity
"""

__version__ = "1.0.0"
