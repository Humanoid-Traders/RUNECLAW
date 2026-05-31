"""
Compatibility layer — re-exports Pydantic if available, else provides
a minimal stdlib fallback so self-tests run in clean environments.
"""
try:
    from pydantic import BaseModel, Field
    HAS_PYDANTIC = True
except ImportError:
    from dataclasses import dataclass, field as _field
    HAS_PYDANTIC = False

    class BaseModel:
        """Minimal fallback — not a real Pydantic model."""
        def model_dump(self):
            return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

    def Field(default=None, **kwargs):
        return default
