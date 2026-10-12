"""Core module."""

from . import util
from .util import helper
import os


class Engine(object):
    """Runs things."""
    
    def start(self, speed=3):
        """Start the engine."""
        pass
    
    def stop(self):
        """Stop the engine."""
        pass


def build(name, *args, retries=2, **kw):
    """Build it."""
    pass
