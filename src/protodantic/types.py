"""Range-constrained integer annotations matching proto scalar domains."""

from typing import Annotated

from pydantic import Field

Int32 = Annotated[int, Field(ge=-(2**31), le=2**31 - 1)]
UInt32 = Annotated[int, Field(ge=0, le=2**32 - 1)]
Int64 = Annotated[int, Field(ge=-(2**63), le=2**63 - 1)]
UInt64 = Annotated[int, Field(ge=0, le=2**64 - 1)]
