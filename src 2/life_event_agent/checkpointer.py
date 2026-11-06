"""Checkpointer setup.

DATABASE_URL set   -> PostgresSaver (durable: survives process restarts,
                       supports resume-after-crash and time travel)
DATABASE_URL unset -> InMemorySaver (tests, quick local runs)

Both use a serializer that explicitly allowlists our Pydantic state
types, so checkpoints round-trip as real models instead of dicts.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from .state import Classification, Finding, Plan, PlanTask, Review

SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=[Classification, Finding, Plan, PlanTask, Review]
)


@contextmanager
def postgres_checkpointer(url: str) -> Iterator[BaseCheckpointSaver]:
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection
    from psycopg.rows import dict_row

    # PostgresSaver needs autocommit + dict rows; prepare_threshold=0
    # keeps it safe behind pgbouncer-style poolers.
    with Connection.connect(
        url, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        saver = PostgresSaver(conn, serde=SERDE)
        saver.setup()  # idempotent: creates/migrates checkpoint tables
        yield saver


@contextmanager
def get_checkpointer() -> Iterator[BaseCheckpointSaver]:
    url = os.getenv("DATABASE_URL")
    if url:
        with postgres_checkpointer(url) as saver:
            yield saver
    else:
        yield InMemorySaver(serde=SERDE)
