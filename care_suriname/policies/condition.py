"""Persisted clinical-domain vocabulary for Suriname diagnosis commands."""

from enum import Enum


class ClinicalDomainChoices(str, Enum):
    general = "general"
    urology = "urology"
