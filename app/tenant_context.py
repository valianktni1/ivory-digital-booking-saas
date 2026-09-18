from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import Membership, Tenant, TenantStatus, User


def set_database_tenant(db: Session, tenant_id: str | None = None,
                        platform_admin: bool = False) -> None:
    """Set PostgreSQL transaction-local RLS context; SQLite tests use app guards."""
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    db.execute(text("SELECT set_config('app.platform_admin', :value, true)"), {
        "value": "true" if platform_admin else "false",
    })
    db.execute(text("SELECT set_config('app.tenant_id', :value, true)"), {
        "value": tenant_id or "",
    })


def membership_for(db: Session, user: User, tenant_id: str | None = None) -> Membership:
    stmt = select(Membership).where(Membership.user_id == user.id)
    if tenant_id:
        stmt = stmt.where(Membership.tenant_id == tenant_id)
    membership = db.scalar(stmt.order_by(Membership.created_at).limit(1))
    if not membership:
        raise HTTPException(403, "You do not have access to this business")
    tenant = db.get(Tenant, membership.tenant_id)
    if not tenant or tenant.status in {TenantStatus.SUSPENDED, TenantStatus.CANCELLED}:
        raise HTTPException(403, "This business account is not currently available")
    trial_end = tenant.trial_ends_at
    if trial_end.tzinfo is None:
        trial_end = trial_end.replace(tzinfo=timezone.utc)
    if tenant.status == TenantStatus.TRIAL and trial_end <= datetime.now(timezone.utc):
        raise HTTPException(403, "This trial has ended. Please contact Ivory Digital to continue")
    set_database_tenant(db, membership.tenant_id)
    return membership


def install_postgres_rls(db: Session) -> None:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    for table_name in ("clients", "bookings"):
        db.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
        db.execute(text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
        db.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {table_name}"))
        db.execute(text(
            f"CREATE POLICY tenant_isolation ON {table_name} USING ("
            "current_setting('app.platform_admin', true) = 'true' OR "
            "tenant_id::text = current_setting('app.tenant_id', true)) "
            "WITH CHECK (current_setting('app.platform_admin', true) = 'true' OR "
            "tenant_id::text = current_setting('app.tenant_id', true))"
        ))
