from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Base(DeclarativeBase):
    """Declarative base.

    Column types are deliberately dialect-neutral (`sa.Uuid`, `sa.JSON`) so the
    same models run on PostgreSQL in production and SQLite in tests.
    """

    type_annotation_map = {dict: sa.JSON, list: sa.JSON}


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=sa.func.now(),
        nullable=False,
    )


def enum_column(enum_cls, **kwargs):
    """Store enums as validated VARCHAR so migrations stay simple across dialects.

    `values_callable` persists the member *value* ('active'), not the member
    name ('ACTIVE'), which SQLAlchemy would use by default. Raw SQL predicates
    such as the partial index on live assignments match on those values.
    """
    return mapped_column(
        sa.Enum(
            enum_cls,
            native_enum=False,
            length=32,
            validate_strings=True,
            values_callable=lambda cls: [member.value for member in cls],
        ),
        **kwargs,
    )
