from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Address, Contact
from app.schemas import AddressCreate, ContactCreate, ContactReplace, ContactUpdate

SORTABLE_FIELDS = ("id", "first_name", "last_name", "email", "company", "created_at", "updated_at")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def get_contact(db: Session, contact_id: int) -> Contact | None:
    stmt = select(Contact).options(selectinload(Contact.addresses)).where(Contact.id == contact_id)
    return db.execute(stmt).scalar_one_or_none()


def get_contact_by_email(db: Session, email: str) -> Contact | None:
    stmt = select(Contact).where(func.lower(Contact.email) == _normalize_email(email))
    return db.execute(stmt).scalar_one_or_none()


def count_contacts(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Contact)).scalar_one()


def list_contacts(
    db: Session,
    *,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "id",
    order: str = "asc",
) -> tuple[list[Contact], int]:
    """Return (page of contacts, total matching count)."""
    stmt = select(Contact)

    if search:
        pattern = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Contact.first_name).like(pattern),
                func.lower(Contact.last_name).like(pattern),
                func.lower(Contact.email).like(pattern),
                func.lower(func.coalesce(Contact.company, "")).like(pattern),
                func.lower(func.coalesce(Contact.phone, "")).like(pattern),
            )
        )

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    if sort_by not in SORTABLE_FIELDS:
        sort_by = "id"
    column = getattr(Contact, sort_by)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())

    items = db.execute(stmt.options(selectinload(Contact.addresses)).limit(limit).offset(offset)).scalars().all()
    return list(items), total


def _new_addresses(addresses: list[AddressCreate]) -> list[Address]:
    return [Address(**address.model_dump()) for address in addresses]


def _replace_addresses(contact: Contact, addresses: list[AddressCreate]) -> None:
    """Replace a contact's complete address collection in the active transaction."""
    contact.addresses = _new_addresses(addresses)


def create_contact(db: Session, payload: ContactCreate) -> Contact:
    data = payload.model_dump(exclude={"addresses"})
    data["email"] = _normalize_email(data["email"])
    contact = Contact(**data, addresses=_new_addresses(payload.addresses))
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def replace_contact(db: Session, contact: Contact, payload: ContactReplace) -> Contact:
    data = payload.model_dump(exclude={"addresses"})
    for field, value in data.items():
        # Photo uploads can be large and contact-edit forms commonly omit their
        # existing value. Unlike the other optional PUT fields, leave it intact
        # unless the client explicitly sends a replacement or null.
        if field == "photo" and field not in payload.model_fields_set:
            continue
        setattr(contact, field, _normalize_email(value) if field == "email" else value)
    _replace_addresses(contact, payload.addresses)
    db.commit()
    db.refresh(contact)
    return contact


def update_contact(db: Session, contact: Contact, payload: ContactUpdate) -> Contact:
    data = payload.model_dump(exclude={"addresses"}, exclude_unset=True)
    addresses = payload.addresses if "addresses" in payload.model_fields_set else None
    for field, value in data.items():
        setattr(contact, field, _normalize_email(value) if field == "email" else value)
    if addresses is not None:
        _replace_addresses(contact, addresses)
    db.commit()
    db.refresh(contact)
    return contact


def delete_contact(db: Session, contact: Contact) -> None:
    db.delete(contact)
    db.commit()
