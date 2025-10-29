from datetime import datetime
from .models import OptionFeature
from typing import Dict

def extract_features_from_snapshot(snapshot: Dict) -> OptionFeature:
    """Convert raw snapshot JSON to OptionFeature dataclass."""
    expiry_str = snapshot.get("expiryDate")
    timestamp_str = snapshot.get("timestamp")

    days_to_exp = 0
    if expiry_str and timestamp_str:
        try:
            expiry_dt = datetime.fromisoformat(expiry_str)
            timestamp_dt = datetime.fromisoformat(timestamp_str)
            days_to_exp = (expiry_dt - timestamp_dt).days
        except:
            days_to_exp = 0

    bid = float(snapshot.get("bid", 0))
    ask = float(snapshot.get("ask", 0))
    near = float(snapshot.get("nearPrice", 0))
    strike = float(snapshot.get("strikePrice", 0))
    mid_price = (bid + ask) / 2 if bid and ask else None
    spread = ask - bid if bid and ask else None
    moneyness = (near - strike) / near if near else None

    g = snapshot.get("greeks", {}) or {}

    return OptionFeature(
        symbol=snapshot.get("symbol", ""),
        osiKey=snapshot.get("osiKey", ""),
        optionType=1 if str(snapshot.get("optionType", "CALL")).upper() == "CALL" else 0,
        strikePrice=strike,
        lastPrice=float(snapshot.get("lastPrice", 0)),
        bid=bid,
        ask=ask,
        bidSize=float(snapshot.get("bidSize", 0)),
        askSize=float(snapshot.get("askSize", 0)),
        volume=float(snapshot.get("volume", 0)),
        openInterest=float(snapshot.get("openInterest", 0)),
        nearPrice=near,
        inTheMoney=1 if str(snapshot.get("inTheMoney", "n")).lower().startswith("y") else 0,
        delta=float(g.get("delta", 0)),
        gamma=float(g.get("gamma", 0)),
        theta=float(g.get("theta", 0)),
        vega=float(g.get("vega", 0)),
        rho=float(g.get("rho", 0)),
        iv=float(g.get("iv", 0)),
        daysToExpiration=days_to_exp,
        spread=spread,
        midPrice=mid_price,
        moneyness=moneyness
    )

def features_to_array(feature: OptionFeature):
    """Convert OptionFeature into a numeric array for ML models."""
    return [
        feature.optionType,
        feature.strikePrice,
        feature.lastPrice,
        feature.bid,
        feature.ask,
        feature.bidSize,
        feature.askSize,
        feature.volume,
        feature.openInterest,
        feature.nearPrice,
        feature.inTheMoney,
        feature.delta,
        feature.gamma,
        feature.theta,
        feature.vega,
        feature.rho,
        feature.iv,
        feature.spread or 0,
        feature.midPrice or 0,
        feature.moneyness or 0,
        feature.daysToExpiration
    ]
