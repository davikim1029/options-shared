from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from dataclasses import asdict as dataclass_asdict


class OptionFeatures(BaseModel):
    symbol: str
    osiKey: Optional[str] = None
    optionType: int                     # 1 = CALL, 0 = PUT
    strikePrice: float
    lastPrice: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    bidSize: Optional[float] = None
    askSize: Optional[float] = None
    openInterest: Optional[float] = None
    volume: Optional[float] = None
    inTheMoney: int                     # 1 = yes, 0 = no
    nearPrice: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    rho: Optional[float] = None
    iv: Optional[float] = None
    daysToExpiration: Optional[float] = None
    spread: Optional[float] = None
    midPrice: Optional[float] = None
    moneyness: Optional[float] = None
    sentiment: Optional[float] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def to_dict(self) -> dict:
        """Return a clean dict representation (for ML model or JSON export)."""
        d = self.dict()
        d["timestamp"] = self.timestamp.isoformat()
        return d

    def asdict(self) -> dict:
        """Dataclass-style compatibility wrapper for code using asdict()."""
        return self.to_dict()

    class Config:
        # Allow dataclass-like behavior and type coercion
        arbitrary_types_allowed = True
        orm_mode = True
        allow_mutation = True
        validate_assignment = True
