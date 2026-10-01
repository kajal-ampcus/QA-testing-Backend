"""Named values generated against an observed field, never credentials."""

from typing import Literal

from pydantic import BaseModel, Field


class FieldDatum(BaseModel):
    key: str
    name: str
    value: str
    category: Literal["POSITIVE", "NEGATIVE", "EDGE_CASE"]
    source: str = Field(default="OBSERVED_DOM")
