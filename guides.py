"""支付、退税、签证、行程等说明页路由。"""
from fastapi import APIRouter

from community import _page

router = APIRouter()


@router.get("/pay")
def p_pay():
    return _page("pay.html", "/pay")


@router.get("/tax-refund")
def p_tax():
    return _page("tax-refund.html", "/tax-refund")


@router.get("/visa")
def p_visa():
    return _page("visa.html", "/visa")


@router.get("/itineraries")
def p_itineraries():
    return _page("itineraries.html", "/itineraries")


@router.get("/rate")
def p_rate_alias():
    return _page("rate.html", "/community/rate")
