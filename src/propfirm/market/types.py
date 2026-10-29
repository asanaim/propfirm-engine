"""The price update: the one record every price source produces."""

from dataclasses import dataclass

from propfirm._checks import require_int, require_text


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """One price for one symbol, as the engine receives it.

    The feed gives a single price per update, with no bid or ask; the engine
    derives bid and ask from it (step 4).

    Attributes:
        symbol: Launch symbol name, e.g. "EUR/USD".
        seq: Per-symbol sequence number, contiguous from 1, assigned by the
            feed connector. A jump means updates were lost in transport.
        source_ts: When the source produced the price, ms since epoch UTC.
        receive_ts: When the connector received it, ms since epoch UTC. The
            delay guard compares the two (step 4). Not required to be
            >= source_ts: real clocks can disagree.
        price: Price in integer points (price = points / 10**digits).
    """

    symbol: str
    seq: int
    source_ts: int
    receive_ts: int
    price: int

    def __post_init__(self) -> None:
        require_text("symbol", self.symbol)
        require_int("seq", self.seq, minimum=1)
        require_int("source_ts", self.source_ts, minimum=0)
        require_int("receive_ts", self.receive_ts, minimum=0)
        require_int("price", self.price, minimum=1)
