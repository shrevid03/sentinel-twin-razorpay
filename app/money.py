"""Money in Sentinel Twin is Indian rupees (INR) everywhere: catalog, mandate, twin traffic, policy thresholds,
red-team attacks, drill costs and Razorpay charges.

The twin was first calibrated in US dollars. Every money constant is written as ``<old dollar value> * K`` so the
simulation draws the same random numbers and the risk engine makes the same decisions, just in rupees.
"""
from __future__ import annotations

K = 84.0  # rupees per original calibration unit (the old USD figure); only used to scale constants


def rs(x, decimals: int = 0) -> str:
    """Format rupees with Indian digit grouping: rs(123456.5) -> '₹1,23,457', rs(2688, 2) -> '₹2,688.00'."""
    x = float(x or 0)
    sign = "-" if x < 0 else ""
    whole, _, frac = f"{abs(x):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}₹{whole}" + (f".{frac}" if decimals else "")
