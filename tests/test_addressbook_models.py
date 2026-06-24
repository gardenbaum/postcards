"""Unit tests for the addressbook data models.

These tests exercise the typed dataclasses in
:mod:`postcards.addressbook.models` directly. They do not touch
disk — the persistence layer (see
:mod:`postcards.addressbook.storage`) is tested separately in
``test_addressbook_storage.py``.

The suite covers:

* :class:`AddressCategory.from_string` and its aliases;
* :class:`AddressBookEntry` validation and round-tripping
  through ``to_dict`` / ``from_dict``;
* :class:`AddressBook` value-type semantics (``add`` /
  ``update`` / ``remove`` return new books);
* :class:`MessageTemplate` validation and rendering;
* :class:`TemplateBook` value-type semantics.

Each ``frozen=True`` dataclass is exercised for immutability
because the rest of the codebase relies on the value-type
discipline.
"""

from __future__ import annotations

import dataclasses

import pytest

from postcards.addressbook.models import (
    MAX_NAME_LENGTH,
    AddressBook,
    AddressBookEntry,
    AddressCategory,
    MessageTemplate,
    TemplateBook,
    TemplateError,
)
from postcards.backend.base import AddressSpec

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def alice_address() -> AddressSpec:
    return AddressSpec(
        prename="Alice",
        lastname="Zuercher",
        street="Bahnhofstrasse 1",
        zip_code="8000",
        place="Zurich",
    )


@pytest.fixture
def bob_address() -> AddressSpec:
    return AddressSpec(
        prename="Bob",
        lastname="Muster",
        street="Hauptstrasse 5",
        zip_code="3000",
        place="Bern",
        salutation="Mr.",
    )


# ---------------------------------------------------------------------------
# AddressCategory
# ---------------------------------------------------------------------------


class TestAddressCategory:
    def test_enum_values_match_swiss_post_terminology(self) -> None:
        assert AddressCategory.RECIPIENT.value == "recipient"
        assert AddressCategory.SENDER.value == "sender"

    def test_from_string_accepts_canonical_names(self) -> None:
        assert AddressCategory.from_string("recipient") is AddressCategory.RECIPIENT
        assert AddressCategory.from_string("sender") is AddressCategory.SENDER

    def test_from_string_accepts_to_and_from_aliases(self) -> None:
        assert AddressCategory.from_string("to") is AddressCategory.RECIPIENT
        assert AddressCategory.from_string("from") is AddressCategory.SENDER

    def test_from_string_is_case_insensitive(self) -> None:
        assert AddressCategory.from_string("RECIPIENT") is AddressCategory.RECIPIENT
        assert AddressCategory.from_string(" Sender ") is AddressCategory.SENDER

    def test_from_string_rejects_unknown_values(self) -> None:
        with pytest.raises(TemplateError, match="unknown address category"):
            AddressCategory.from_string("neighbour")


# ---------------------------------------------------------------------------
# AddressBookEntry
# ---------------------------------------------------------------------------


class TestAddressBookEntry:
    def test_construction_stores_all_fields(self, alice_address: AddressSpec) -> None:
        entry = AddressBookEntry(
            name="alice",
            category=AddressCategory.RECIPIENT,
            address=alice_address,
            notes="vacation 2024",
        )
        assert entry.name == "alice"
        assert entry.category is AddressCategory.RECIPIENT
        assert entry.address == alice_address
        assert entry.notes == "vacation 2024"

    def test_construction_defaults_notes_to_empty(self, alice_address: AddressSpec) -> None:
        entry = AddressBookEntry(
            name="alice",
            category=AddressCategory.RECIPIENT,
            address=alice_address,
        )
        assert entry.notes == ""

    def test_is_frozen(self, alice_address: AddressSpec) -> None:
        entry = AddressBookEntry(
            name="alice",
            category=AddressCategory.RECIPIENT,
            address=alice_address,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.name = "mallory"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "bad_name",
        [
            "",
            "   ",
            "Alice",  # uppercase letters are not allowed
            "alice bob",  # spaces not allowed
            "alice!",
            "a" * (MAX_NAME_LENGTH + 1),
        ],
    )
    def test_rejects_invalid_names(self, bad_name: str, alice_address: AddressSpec) -> None:
        with pytest.raises(TemplateError):
            AddressBookEntry(
                name=bad_name,
                category=AddressCategory.RECIPIENT,
                address=alice_address,
            )

    def test_accepts_names_starting_with_digit(self, alice_address: AddressSpec) -> None:
        # Names starting with a digit are valid (e.g. ``1john``),
        # so long as the rest of the identifier matches the
        # pattern. This case is the one parameterised assertion
        # above explicitly carves out.
        entry = AddressBookEntry(
            name="1alice",
            category=AddressCategory.RECIPIENT,
            address=alice_address,
        )
        assert entry.name == "1alice"

    def test_to_dict_round_trips_through_from_dict(self, alice_address: AddressSpec) -> None:
        original = AddressBookEntry(
            name="alice",
            category=AddressCategory.RECIPIENT,
            address=alice_address,
            notes="friend",
        )
        payload = original.to_dict()
        restored = AddressBookEntry.from_dict(payload)
        assert restored == original

    def test_to_dict_round_trips_with_optional_fields(self, bob_address: AddressSpec) -> None:
        original = AddressBookEntry(
            name="bob",
            category=AddressCategory.SENDER,
            address=bob_address,
            notes="",
        )
        restored = AddressBookEntry.from_dict(original.to_dict())
        assert restored == original

    def test_from_dict_coerces_category_string(self, alice_address: AddressSpec) -> None:
        # When loading from JSON the ``category`` field is a plain
        # string; ``from_dict`` must coerce it to the enum.
        payload = {
            "name": "alice",
            "category": "recipient",
            "address": {
                "prename": alice_address.prename,
                "lastname": alice_address.lastname,
                "street": alice_address.street,
                "zip_code": alice_address.zip_code,
                "place": alice_address.place,
            },
            "notes": "",
        }
        entry = AddressBookEntry.from_dict(payload)
        assert entry.category is AddressCategory.RECIPIENT

    def test_from_dict_rejects_missing_required_field(self) -> None:
        with pytest.raises(TemplateError, match="missing required field"):
            AddressBookEntry.from_dict({"category": "recipient", "address": {}})

    def test_from_dict_rejects_non_mapping_address(self) -> None:
        with pytest.raises(TemplateError, match="'address' must be a mapping"):
            AddressBookEntry.from_dict(
                {"name": "alice", "category": "recipient", "address": "not-a-mapping"}
            )

    def test_rejects_non_address_spec(self) -> None:
        with pytest.raises(TemplateError, match="must be an AddressSpec"):
            AddressBookEntry(
                name="alice",
                category=AddressCategory.RECIPIENT,
                address="not-an-address-spec",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# AddressBook
# ---------------------------------------------------------------------------


def _make_entry(name: str, category: AddressCategory, place: str = "Zurich") -> AddressBookEntry:
    return AddressBookEntry(
        name=name,
        category=category,
        address=AddressSpec(
            prename=name.title(),
            lastname="Tester",
            street="Teststrasse 1",
            zip_code="8000",
            place=place,
        ),
    )


class TestAddressBook:
    def test_default_construction_is_empty(self) -> None:
        book = AddressBook()
        assert book.is_empty()
        assert len(book) == 0
        assert book.names() == ()

    def test_construction_rejects_duplicate_names(self) -> None:
        with pytest.raises(TemplateError, match="duplicate entry name"):
            AddressBook(entries=(_make_entry("alice", AddressCategory.RECIPIENT),) * 2)

    def test_add_returns_new_book_with_appended_entry(self) -> None:
        book = AddressBook()
        entry = _make_entry("alice", AddressCategory.RECIPIENT)
        new_book = book.add(entry)
        assert new_book is not book
        assert len(new_book) == 1
        assert new_book.get("alice") == entry
        # The original is untouched.
        assert book.is_empty()

    def test_add_rejects_duplicate(self) -> None:
        book = AddressBook(entries=(_make_entry("alice", AddressCategory.RECIPIENT),))
        with pytest.raises(TemplateError, match="already exists"):
            book.add(_make_entry("alice", AddressCategory.RECIPIENT))

    def test_get_raises_for_unknown_name(self) -> None:
        book = AddressBook()
        with pytest.raises(TemplateError, match="no address-book entry named"):
            book.get("alice")

    def test_find_returns_none_for_unknown_name(self) -> None:
        book = AddressBook()
        assert book.find("alice") is None

    def test_update_replaces_in_place_preserving_order(self) -> None:
        book = AddressBook(
            entries=(
                _make_entry("alice", AddressCategory.RECIPIENT),
                _make_entry("bob", AddressCategory.RECIPIENT),
                _make_entry("carol", AddressCategory.RECIPIENT),
            )
        )
        new_entry = AddressBookEntry(
            name="bob",
            category=AddressCategory.SENDER,  # changed category
            address=_make_entry("bob", AddressCategory.RECIPIENT).address,
        )
        updated = book.update(new_entry)
        assert updated.names() == ("alice", "bob", "carol")  # order preserved
        assert updated.get("bob").category is AddressCategory.SENDER

    def test_update_rejects_unknown_name(self) -> None:
        book = AddressBook()
        with pytest.raises(TemplateError, match="cannot update unknown"):
            book.update(_make_entry("alice", AddressCategory.RECIPIENT))

    def test_remove_returns_book_without_entry(self) -> None:
        book = AddressBook(
            entries=(
                _make_entry("alice", AddressCategory.RECIPIENT),
                _make_entry("bob", AddressCategory.RECIPIENT),
            )
        )
        trimmed = book.remove("alice")
        assert trimmed.names() == ("bob",)

    def test_remove_rejects_unknown_name(self) -> None:
        book = AddressBook()
        with pytest.raises(TemplateError, match="cannot remove unknown"):
            book.remove("alice")

    def test_filter_by_category(self) -> None:
        book = AddressBook(
            entries=(
                _make_entry("alice", AddressCategory.RECIPIENT),
                _make_entry("home", AddressCategory.SENDER),
                _make_entry("bob", AddressCategory.RECIPIENT),
            )
        )
        recipients = book.filter(category=AddressCategory.RECIPIENT)
        assert recipients.names() == ("alice", "bob")
        senders = book.filter(category=AddressCategory.SENDER)
        assert senders.names() == ("home",)

    def test_filter_with_none_returns_copy(self) -> None:
        book = AddressBook(
            entries=(
                _make_entry("alice", AddressCategory.RECIPIENT),
                _make_entry("bob", AddressCategory.RECIPIENT),
            )
        )
        copy = book.filter()
        assert copy.names() == book.names()
        assert copy is not book

    def test_iter_returns_entries_in_order(self) -> None:
        book = AddressBook(
            entries=(
                _make_entry("alice", AddressCategory.RECIPIENT),
                _make_entry("bob", AddressCategory.RECIPIENT),
            )
        )
        assert list(book) == list(book.entries)

    def test_to_dict_round_trips_through_from_dict(self) -> None:
        book = AddressBook(
            entries=(
                _make_entry("alice", AddressCategory.RECIPIENT),
                _make_entry("home", AddressCategory.SENDER),
            )
        )
        restored = AddressBook.from_dict(book.to_dict())
        assert restored == book

    def test_from_dict_rejects_unsupported_version(self) -> None:
        with pytest.raises(TemplateError, match="unsupported address-book version"):
            AddressBook.from_dict({"version": 99, "entries": []})

    def test_from_dict_rejects_missing_version(self) -> None:
        with pytest.raises(TemplateError, match="missing 'version' field"):
            AddressBook.from_dict({"entries": []})

    def test_from_dict_rejects_non_list_entries(self) -> None:
        with pytest.raises(TemplateError, match="'entries' must be a list"):
            AddressBook.from_dict({"version": 1, "entries": "nope"})


# ---------------------------------------------------------------------------
# MessageTemplate
# ---------------------------------------------------------------------------


class TestMessageTemplate:
    def test_construction_stores_body_and_description(self) -> None:
        template = MessageTemplate(
            name="greeting",
            body="Hi $name, greetings from Zurich",
            description="default greeting",
        )
        assert template.name == "greeting"
        assert template.body == "Hi $name, greetings from Zurich"
        assert template.description == "default greeting"

    def test_description_defaults_to_empty(self) -> None:
        template = MessageTemplate(name="greeting", body="Hi")
        assert template.description == ""

    def test_is_frozen(self) -> None:
        template = MessageTemplate(name="greeting", body="Hi")
        with pytest.raises(dataclasses.FrozenInstanceError):
            template.body = "changed"  # type: ignore[misc]

    @pytest.mark.parametrize("bad_name", ["", " ", "Greeting", "with space", "x" * 65])
    def test_rejects_invalid_names(self, bad_name: str) -> None:
        with pytest.raises(TemplateError):
            MessageTemplate(name=bad_name, body="Hi")

    def test_render_substitutes_simple_variable(self) -> None:
        template = MessageTemplate(name="greeting", body="Hi $name!")
        assert template.render({"name": "Alice"}) == "Hi Alice!"

    def test_render_substitutes_braced_variable(self) -> None:
        template = MessageTemplate(name="greeting", body="Hi ${name}!")
        assert template.render({"name": "Alice"}) == "Hi Alice!"

    def test_render_escapes_dollar_sign(self) -> None:
        template = MessageTemplate(name="price", body="Price: $$5")
        assert template.render({}) == "Price: $5"

    def test_render_coerces_values_to_strings(self) -> None:
        template = MessageTemplate(name="count", body="Sent: $count cards")
        assert template.render({"count": 42}) == "Sent: 42 cards"

    def test_to_dict_round_trips_through_from_dict(self) -> None:
        template = MessageTemplate(
            name="greeting",
            body="Hi $name",
            description="default",
        )
        assert MessageTemplate.from_dict(template.to_dict()) == template

    def test_from_dict_rejects_missing_body(self) -> None:
        with pytest.raises(TemplateError, match="missing required field"):
            MessageTemplate.from_dict({"name": "greeting"})


# ---------------------------------------------------------------------------
# TemplateBook
# ---------------------------------------------------------------------------


def _make_template(name: str, body: str = "Hi $name") -> MessageTemplate:
    return MessageTemplate(name=name, body=body)


class TestTemplateBook:
    def test_default_construction_is_empty(self) -> None:
        book = TemplateBook()
        assert book.is_empty()
        assert book.names() == ()

    def test_construction_rejects_duplicate_names(self) -> None:
        with pytest.raises(TemplateError, match="duplicate template name"):
            TemplateBook(templates=(_make_template("greeting"),) * 2)

    def test_add_returns_new_book_with_appended_template(self) -> None:
        book = TemplateBook()
        template = _make_template("greeting")
        new_book = book.add(template)
        assert new_book is not book
        assert new_book.get("greeting") == template
        assert book.is_empty()

    def test_add_rejects_duplicate(self) -> None:
        book = TemplateBook(templates=(_make_template("greeting"),))
        with pytest.raises(TemplateError, match="already exists"):
            book.add(_make_template("greeting", body="different body"))

    def test_get_raises_for_unknown_name(self) -> None:
        book = TemplateBook()
        with pytest.raises(TemplateError, match="no template named"):
            book.get("greeting")

    def test_find_returns_none_for_unknown_name(self) -> None:
        book = TemplateBook()
        assert book.find("greeting") is None

    def test_update_preserves_order(self) -> None:
        book = TemplateBook(
            templates=(
                _make_template("greeting"),
                _make_template("birthday", body="Happy birthday $name"),
            )
        )
        updated = book.update(MessageTemplate(name="greeting", body="Hi $name!"))
        assert updated.names() == ("greeting", "birthday")
        assert updated.get("greeting").body == "Hi $name!"

    def test_update_rejects_unknown(self) -> None:
        book = TemplateBook()
        with pytest.raises(TemplateError, match="cannot update unknown"):
            book.update(_make_template("greeting"))

    def test_remove(self) -> None:
        book = TemplateBook(
            templates=(
                _make_template("greeting"),
                _make_template("birthday"),
            )
        )
        trimmed = book.remove("greeting")
        assert trimmed.names() == ("birthday",)

    def test_remove_rejects_unknown(self) -> None:
        book = TemplateBook()
        with pytest.raises(TemplateError, match="cannot remove unknown"):
            book.remove("greeting")

    def test_to_dict_round_trips(self) -> None:
        book = TemplateBook(
            templates=(
                _make_template("greeting"),
                _make_template("birthday"),
            )
        )
        restored = TemplateBook.from_dict(book.to_dict())
        assert restored == book

    def test_from_dict_rejects_unsupported_version(self) -> None:
        with pytest.raises(TemplateError, match="unsupported template-book version"):
            TemplateBook.from_dict({"version": 99, "templates": []})

    def test_from_dict_rejects_non_list_templates(self) -> None:
        with pytest.raises(TemplateError, match="'templates' must be a list"):
            TemplateBook.from_dict({"version": 1, "templates": "nope"})

    def test_iter_returns_templates_in_order(self) -> None:
        book = TemplateBook(templates=(_make_template("a"), _make_template("b")))
        assert list(book) == list(book.templates)
