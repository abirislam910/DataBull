"""Model registry.

A SQLAlchemy model only attaches its table to `Base.metadata` when its module is
imported and the class body executes. Alembic's autogenerate and any
`metadata.create_all()` diff against that metadata, so a model that is never
imported is silently missing from migrations. Importing all three here — and
having `env.py` import this package — is what makes the full schema visible.
"""

from app.models.device import Device, DeviceType
from app.models.reading import Reading
from app.models.user import User

__all__ = ["Device", "DeviceType", "Reading", "User"]
