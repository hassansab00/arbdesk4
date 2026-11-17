"""Pure saved-book mark shared by capture and offline research readers."""

def market_price(top):
    """The market's number for a band: the mid of a two-sided book; else the
    last trade held inside the side that is quoted; else None.

    A YES offered at 0.001 is worth no more than 0.001 whatever it last traded
    at. Measured 26 Sep over 429 checkpoints: 970 of 2,493 ask-only books had
    a last trade above their own ask (typically 0.999 over 0.001), and taking
    the last as it stood made that dead bucket the market's favourite. The
    database's public.book_mark() is the same rule."""
    bid, ask, last = top.get("bid"), top.get("ask"), top.get("last")
    if bid is not None and ask is not None:
        return round((bid + ask) / 2, 6)
    if last is None:
        return None
    if ask is not None:
        return min(last, ask)
    if bid is not None:
        return max(last, bid)
    return last

