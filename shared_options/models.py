from pydantic import BaseModel
from typing import Optional
from datetime import datetime,timezone

class OptionFeature(BaseModel):
    symbol: str
    osiKey: str
    timestamp: datetime = datetime.now(timezone.utc)  # automatically set to current UTC time
    optionType: int  # 1 = CALL, 0 = PUT
    strikePrice: float
    lastPrice: float
    bid: float
    ask: float
    bidSize: float
    askSize: float
    volume: float
    openInterest: float
    nearPrice: float
    inTheMoney: int
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    iv: float
    daysToExpiration: float
    spread: Optional[float] = None
    midPrice: Optional[float] = None
    moneyness: Optional[float] = None
