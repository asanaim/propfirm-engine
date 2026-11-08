"""Who may propose and who may approve each kind of setting change.

Rule AC00 [decided 8 Nov 2026]: every decision needs two different
people, one proposes and the other approves; nothing takes effect before
the approval, and the proposer never approves their own proposal.

Rule AC01: a product change (stage limits) is proposed by an owner and
approved by the other owner. While an owner is unavailable, the owners'
stand-in approver (an admin both owners appointed, for a limited time)
may approve in that owner's place.

The stand-in appointment is itself owner-level: one owner proposes it and
the other owner approves it; a stand-in never approves it.

Country gating and the website's text (AC07) stay with admins: an admin
(or an owner) proposes, another admin or owner approves.
"""

from propfirm.settings.overlay import CountryGating, PolicyPage, Setting, StageLimits, StandIn
from propfirm.settings.site import SiteSection
from propfirm.staff.model import Tier

# AC10: a new version of a policy page is owner level too (no stand-in)
OWNER_LEVEL = (StageLimits, StandIn, PolicyPage)


class Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def owner_level(setting: Setting) -> bool:
    return isinstance(setting, OWNER_LEVEL)


def check_proposer(setting: Setting, tier: Tier | None) -> None:
    """Refuse a proposal its maker may not make."""
    if owner_level(setting):
        if tier != Tier.OWNER:
            raise Refused("owners_only", "an owner proposes a product, stand-in or policy change")
        return
    assert isinstance(setting, CountryGating | SiteSection)  # admin level
    if tier is None or tier < Tier.ADMIN:
        raise Refused("forbidden", "an admin or owner proposes this change")


def check_approver(
    setting: Setting,
    proposer: str,
    approver: str,
    tier: Tier | None,
    stand_in: StandIn | None,
) -> str:
    """Refuse an approval the approver may not give; return the capacity it
    is given in ("owner", "stand_in" or "admin")."""
    if approver == proposer:
        raise Refused("four_eyes", "the maker of a change does not approve it")
    if owner_level(setting):
        if tier == Tier.OWNER:
            return "owner"
        if (
            isinstance(setting, StageLimits)
            and stand_in is not None
            and stand_in.subject == approver
        ):
            return "stand_in"
        raise Refused("owners_only", "the other owner (or the owners' stand-in) approves this")
    if tier is None or tier < Tier.ADMIN:
        raise Refused("forbidden", "a second admin (T4) or an owner approves settings")
    return "admin"


def check_appointment(setting: StandIn, now: int, max_days: int, target_tier: Tier | None) -> None:
    """A stand-in must be an admin (T4, not an owner), for a limited time."""
    if setting.subject is None:
        return  # ending the appointment
    if target_tier != Tier.ADMIN:
        raise Refused("invalid", "the stand-in must be an admin (T4), not an owner or staff")
    if setting.until <= now:
        raise Refused("invalid", "the appointment must end in the future")
    if setting.until - now > max_days * 86_400_000:
        raise Refused("invalid", f"an appointment lasts at most {max_days} days")
