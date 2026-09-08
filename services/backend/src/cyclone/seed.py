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
            
    # Seed demo users for judges
    demo_users = [
        {"email": "admin@chakravyooh.in", "role": RoleEnum.SUPER_ADMIN.value, "pass": "admin123"},
        {"email": "commander@chakravyooh.in", "role": RoleEnum.COMMANDER.value, "pass": "commander123"},
        {"email": "analyst@chakravyooh.in", "role": RoleEnum.ANALYST.value, "pass": "analyst123"}
    ]
    for du in demo_users:
        existing_user = (await db.execute(select(User).where(User.email == du["email"]))).scalars().first()
        if not existing_user:
            hashed_pw = bcrypt.hashpw(du["pass"].encode(), bcrypt.gensalt()).decode()
            u = User(email=du["email"], hashed_password=hashed_pw, role=du["role"], is_active=True)
            db.add(u)

    await db.commit()
