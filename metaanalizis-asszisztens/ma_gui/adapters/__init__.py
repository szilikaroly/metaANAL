# -*- coding: utf-8 -*-
"""Plugin-adapterek (validator, figure-forge, composer, presubmit): közös protokoll a ``base``-ben.

A csomag importja csak a ``base``-t tölti be; a konkrét adaptereket (``validator``,
``figureforge``, ``composer``, ``presubmit``) a hívó a saját moduljukból importálja.
"""
from .base import (
    Adapter,
    Capability,
    STATES,
    USABLE_STATES,
    error,
    option,
    validate_option_value,
)

__all__ = ["Adapter", "Capability", "STATES", "USABLE_STATES", "error", "option", "validate_option_value"]
