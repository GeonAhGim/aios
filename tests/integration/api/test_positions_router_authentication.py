"""LB-19: position listing requires authentication."""

from tests.support.positions_router import BASE
from tests.support.positions_router import client as client


async def test_positions_require_authentication(client):
    response = await client.get(BASE)
    assert response.status_code == 401
