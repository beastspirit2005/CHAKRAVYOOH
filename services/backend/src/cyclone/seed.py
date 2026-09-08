import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import Zone, ZoneStatus, User, RoleEnum

async def seed_demo_zones(db: AsyncSession):
    """
    Auto-seeds the 4 critical Chakravyooh demo zones if they don't exist.
    """
    demo_zones = [
        {"name": "Puri", "lat": 19.8135, "lon": 85.8312, "radius": 5000.0},
        {"name": "Bhubaneswar", "lat": 20.2961, "lon": 85.8245, "radius": 10000.0},
        {"name": "Cuttack", "lat": 20.4625, "lon": 85.8830, "radius": 8000.0},
        {"name": "Inland", "lat": 21.4669, "lon": 83.9812, "radius": 20000.0},
    ]
    
    for dz in demo_zones:
        stmt = select(Zone).where(Zone.name == dz["name"])
        existing = (await db.execute(stmt)).scalars().first()
        if not existing:
            z = Zone(
                name=dz["name"],
                center_lat=dz["lat"],
                center_lon=dz["lon"],
                radius_m=dz["radius"],
                status=ZoneStatus.NORMAL.value
            )
            db.add(z)
            
    await db.commit()
