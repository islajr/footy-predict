"""
Footy Predict — Reporting Package

Report generation for CLI, Telegram, and Markdown output formats.
Uses Rich for beautiful terminal output and HTML for Telegram messages.
"""

from src.reporting.formatter import ReportFormatter

__all__ = ["ReportFormatter"]
