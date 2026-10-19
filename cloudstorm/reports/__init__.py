"""
Report generation for cloudstorm.
"""
from .csv import generate_csv_report
from .html import generate_html_report

__all__ = ["generate_csv_report", "generate_html_report"]
