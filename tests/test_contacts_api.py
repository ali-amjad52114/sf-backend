import base64
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import Address, AddressType


BASE = "/api/v1/contacts"
PHOTO = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9JZJwAAAAASUVORK5CYII="


def _address(address_type: str, street: str) -> dict:
    return {
        "type": address_type,
        "address": street,
        "city": "San Francisco",
        "state": "CA",
        "postal_code": "94105",
        "country": "USA",
    }


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "sqlite"


def test_create_contact(client, payload):
    response = client.post(BASE, json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["email"] == "ada@example.com"
    assert body["full_name"] == "Ada Lovelace"
    assert body["created_at"] and body["updated_at"]
    assert body["addresses"] == []


def test_create_contact_with_photo(client, payload):
    response = client.post(BASE, json={**payload, "photo": PHOTO})

    assert response.status_code == 201
    assert response.json()["photo"] == PHOTO


def test_create_rejects_unsafe_photo_data(client, payload):
    too_large_photo = "data:image/png;base64," + base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024)
    ).decode()

    for photo in (
        "data:text/html;base64,PGgxPkhlbGxvPC9oMT4=",
        "data:image/png;base64,not-valid-base64!",
        f"data:image/jpeg;base64,{PHOTO.removeprefix('data:image/png;base64,')}",
        too_large_photo,
    ):
        response = client.post(BASE, json={**payload, "photo": photo})
        assert response.status_code == 422


def test_create_requires_valid_email(client, payload):
    response = client.post(BASE, json={**payload, "email": "not-an-email"})
    assert response.status_code == 422


def test_create_requires_names(client, payload):
    response = client.post(BASE, json={**payload, "first_name": ""})
    assert response.status_code == 422


def test_create_serializes_multiple_normalized_addresses(client, payload):
    response = client.post(
        BASE,
        json={
            **payload,
            "addresses": [
                _address("Home", "1 Market St"),
                _address("Work", "2 Mission St"),
            ],
        },
    )
    assert response.status_code == 201
    addresses = response.json()["addresses"]
    assert [(address["type"], address["address"]) for address in addresses] == [
        ("Home", "1 Market St"),
        ("Work", "2 Mission St"),
    ]
    assert all(address["id"] > 0 for address in addresses)


def test_address_type_is_limited_to_home_work_or_other(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [_address("Vacation", "1 Market St")]})
    assert response.status_code == 422


def test_duplicate_email_conflicts(client, payload):
    assert client.post(BASE, json=payload).status_code == 201
    response = client.post(BASE, json={**payload, "email": "ADA@example.com"})
    assert response.status_code == 409


def test_get_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.get(f"{BASE}/{contact_id}")
    assert response.status_code == 200
    assert response.json()["id"] == contact_id


def test_get_missing_contact_returns_404(client):
    assert client.get(f"{BASE}/9999").status_code == 404


def test_list_pagination_and_total(client, payload):
    for index in range(5):
        client.post(BASE, json={**payload, "email": f"user{index}@example.com"})

    response = client.get(BASE, params={"limit": 2, "offset": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2 and body["offset"] == 2


def test_list_search(client, payload):
    client.post(BASE, json=payload)
    client.post(
        BASE,
        json={
            **payload,
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
            "company": "US Navy",
        },
    )

    hits = client.get(BASE, params={"search": "hopper"}).json()
    assert hits["total"] == 1
    assert hits["items"][0]["last_name"] == "Hopper"

    by_company = client.get(BASE, params={"search": "navy"}).json()
    assert by_company["total"] == 1

    misses = client.get(BASE, params={"search": "nobody"}).json()
    assert misses["total"] == 0


def test_list_sorting(client, payload):
    client.post(BASE, json={**payload, "last_name": "Zhang", "email": "z@example.com"})
    client.post(BASE, json={**payload, "last_name": "Adams", "email": "a@example.com"})

    names = [
        item["last_name"]
        for item in client.get(BASE, params={"sort_by": "last_name", "order": "asc"}).json()["items"]
    ]
    assert names == ["Adams", "Zhang"]


def test_list_rejects_bad_sort_field(client):
    assert client.get(BASE, params={"sort_by": "; DROP TABLE contacts"}).status_code == 422


def test_patch_updates_only_sent_fields(client, payload):
    contact_id = client.post(
        BASE, json={**payload, "addresses": [_address("Home", "1 Market St")]}
    ).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"phone": "+1-000-000-0000"})
    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == "+1-000-000-0000"
    assert body["first_name"] == "Ada"
    assert body["company"] == "Analytical Engines"
    assert [address["address"] for address in body["addresses"]] == ["1 Market St"]


def test_patch_replaces_addresses_only_when_the_collection_is_sent(client, payload):
    contact_id = client.post(
        BASE,
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": payload["email"],
            "addresses": [_address("Home", "1 Market St")],
        },
    ).json()["id"]

    response = client.patch(f"{BASE}/{contact_id}", json={"addresses": [_address("Other", "PO Box 1")]})
    assert response.status_code == 200
    assert [(address["type"], address["address"]) for address in response.json()["addresses"]] == [
        ("Other", "PO Box 1")
    ]

    response = client.patch(f"{BASE}/{contact_id}", json={"addresses": []})
    assert response.status_code == 200
    assert response.json()["addresses"] == []


def test_patch_duplicate_email_conflicts(client, payload):
    first = client.post(BASE, json=payload).json()["id"]
    client.post(BASE, json={**payload, "email": "grace@example.com"})
    response = client.patch(f"{BASE}/{first}", json={"email": "grace@example.com"})
    assert response.status_code == 409


def test_patch_same_email_is_allowed(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"email": payload["email"]})
    assert response.status_code == 200


def test_put_replaces_contact(client, payload):
    contact_id = client.post(
        BASE,
        json={**payload, "addresses": [_address("Home", "1 Market St"), _address("Work", "2 Mission St")]},
    ).json()["id"]
    response = client.put(
        f"{BASE}/{contact_id}",
        json={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
            "addresses": [_address("Work", "3 Howard St")],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Grace Hopper"
    assert body["company"] is None  # omitted fields are cleared by PUT
    assert [(address["type"], address["address"]) for address in body["addresses"]] == [
        ("Work", "3 Howard St")
    ]


def test_put_without_addresses_removes_existing_addresses(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [_address("Home", "1 Market St")]}).json()["id"]
    response = client.put(
        f"{BASE}/{contact_id}",
        json={"first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["addresses"] == []


def test_addresses_are_isolated_between_contacts(client, payload):
    ada_id = client.post(BASE, json={**payload, "addresses": [_address("Home", "1 Market St")]}).json()["id"]
    grace_id = client.post(
        BASE,
        json={
            **payload,
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
            "addresses": [_address("Work", "2 Mission St")],
        },
    ).json()["id"]

    assert client.patch(f"{BASE}/{ada_id}", json={"addresses": [_address("Other", "PO Box 1")]}).status_code == 200
    grace_addresses = client.get(f"{BASE}/{grace_id}").json()["addresses"]
    assert [(address["type"], address["address"]) for address in grace_addresses] == [("Work", "2 Mission St")]


def test_address_rows_are_owned_by_the_contact_and_cascade_on_delete(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [_address("Home", "1 Market St")]}).json()["id"]

    with SessionLocal() as db:
        addresses = db.scalars(select(Address).where(Address.contact_id == contact_id)).all()
        assert len(addresses) == 1
        assert addresses[0].contact_id == contact_id

    assert client.delete(f"{BASE}/{contact_id}").status_code == 204
    with SessionLocal() as db:
        assert db.scalars(select(Address).where(Address.contact_id == contact_id)).all() == []


def test_database_rejects_an_orphan_address(client):
    with SessionLocal() as db:
        db.add(
            Address(
                contact_id=9999,
                type=AddressType.HOME,
                address="1 Market St",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_edits_preserve_omitted_photo_and_allow_explicit_clear(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PHOTO}).json()["id"]

    put = client.put(
        f"{BASE}/{contact_id}",
        json={"first_name": "Ada", "last_name": "Byron", "email": payload["email"]},
    )
    assert put.status_code == 200
    assert put.json()["photo"] == PHOTO

    patch = client.patch(f"{BASE}/{contact_id}", json={"notes": "Edited without replacing photo."})
    assert patch.status_code == 200
    assert patch.json()["photo"] == PHOTO
    assert client.get(f"{BASE}/{contact_id}").json()["photo"] == PHOTO

    clear = client.put(
        f"{BASE}/{contact_id}",
        json={
            "first_name": "Ada",
            "last_name": "Byron",
            "email": payload["email"],
            "photo": None,
        },
    )
    assert clear.status_code == 200
    assert clear.json()["photo"] is None


def test_put_missing_contact_returns_404(client):
    response = client.put(
        f"{BASE}/9999",
        json={"first_name": "A", "last_name": "B", "email": "ab@example.com"},
    )
    assert response.status_code == 404


def test_delete_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    assert client.delete(f"{BASE}/{contact_id}").status_code == 204
    assert client.get(f"{BASE}/{contact_id}").status_code == 404
    assert client.delete(f"{BASE}/{contact_id}").status_code == 404


def test_root_lists_entrypoints(client):
    body = client.get("/").json()
    assert body["contacts"] == BASE
