"""The price update: the one record every price source produces."""

from dataclasses import dataclass

from propfirm._checks import require_int, require_text
from propfirm.journal.codec import added_field


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """One price for one symbol, as the engine receives it.

    The feed gives one price per update; the engine derives bid and ask
    from it (step 4). A source that also quotes bid and ask sends them too
    [decided 13 Nov 2026, D-13.10]: the engine then never quotes tighter
    than the source (`propfirm.market.spread`).

    Attributes:
        symbol: Launch symbol name, e.g. "EUR/USD".
        seq: Per-symbol sequence number, contiguous from 1, assigned by the
            feed connector. A jump means updates were lost in transport.
        source_ts: When the source produced the price, ms since epoch UTC.
        receive_ts: When the connector received it, ms since epoch UTC. The
            delay guard compares the two (step 4). Not required to be
            >= source_ts: real clocks can disagree.
        price: Price in integer points (price = points / 10**digits).
        bid, ask: The source's own bid and ask in points, both or neither;
            None for a source that sends one price (every record written
            before 13 Nov 2026 has neither, so its encoding is unchanged).
    """

    symbol: str
    seq: int
    source_ts: int
    receive_ts: int
    price: int
    bid: int | None = added_field(None)
    ask: int | None = added_field(None)

    def __post_init__(self) -> None:
        require_text("symbol", self.symbol)
        require_int("seq", self.seq, minimum=1)
        require_int("source_ts", self.source_ts, minimum=0)
        require_int("receive_ts", self.receive_ts, minimum=0)
        require_int("price", self.price, minimum=1)
        if (self.bid is None) != (self.ask is None):
            raise ValueError("bid and ask come together or not at all")
        if self.bid is not None and self.ask is not None:
            require_int("bid", self.bid, minimum=1)
            require_int("ask", self.ask, minimum=1)
            if self.bid > self.ask:
                raise ValueError("bid must not exceed ask")
