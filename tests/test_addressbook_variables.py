"""Unit tests for :mod:`postcards.addressbook.variables`.

The substitution rules live in
:func:`postcards.addressbook.variables.render_template`. The
tests cover the happy paths (``$name``, ``${name}``, the ``$$``
escape), the strict missing-key semantics, and the error
surfaces the CLI relies on for invalid template syntax.
"""

from __future__ import annotations

import pytest

from postcards.addressbook.variables import TemplateRenderError, render_template


class TestRenderTemplate:
    def test_substitutes_simple_variable(self) -> None:
        assert render_template("Hi $name", {"name": "Alice"}) == "Hi Alice"

    def test_substitutes_braced_variable(self) -> None:
        assert render_template("Hi ${name}", {"name": "Alice"}) == "Hi Alice"

    def test_braced_form_disambiguates_following_text(self) -> None:
        # ``$name!`` would render literally because ``!`` is
        # not a valid identifier character; ``${name}!`` is the
        # form the user actually wants.
        assert render_template("Hello ${name}!", {"name": "Bob"}) == "Hello Bob!"

    def test_escapes_dollar_sign(self) -> None:
        assert render_template("Price: $$5", {}) == "Price: $5"

    def test_multiple_variables(self) -> None:
        body = "Dear $name from $city, greetings"
        assert render_template(body, {"name": "Alice", "city": "Zurich"}) == (
            "Dear Alice from Zurich, greetings"
        )

    def test_repeated_variable(self) -> None:
        assert render_template("$x-$x-$x", {"x": "ab"}) == "ab-ab-ab"

    def test_unsubstituted_variable_raises(self) -> None:
        with pytest.raises(TemplateRenderError, match="undefined variable"):
            render_template("Hi $name", {})

    def test_braced_unsubstituted_variable_raises(self) -> None:
        with pytest.raises(TemplateRenderError, match="undefined variable"):
            render_template("Hi ${name}", {})

    def test_invalid_placeholder_identifier_raises(self) -> None:
        # Identifiers must start with a letter or underscore and
        # contain only letters, digits and underscores.
        with pytest.raises(TemplateRenderError, match="invalid placeholder"):
            render_template("Hi ${1bad}", {})

    def test_invalid_placeholder_with_dash_raises(self) -> None:
        with pytest.raises(TemplateRenderError, match="invalid placeholder"):
            render_template("Hi ${my-name}", {})

    def test_coerces_non_string_values(self) -> None:
        assert render_template("Count: $count", {"count": 42}) == "Count: 42"

    def test_none_value_coerces_to_string_none(self) -> None:
        # ``None`` becomes the string ``"None"``. The user
        # has to opt in to an empty string by passing
        # ``{"name": ""}`` — silent dropping of a ``None``
        # would mask a template-author bug.
        assert render_template("Hi $name", {"name": None}) == "Hi None"

    def test_extra_variables_are_ignored(self) -> None:
        # Unknown variables the user supplied on the CLI are
        # silently dropped — substitution is one-way.
        assert render_template("Hi $name", {"name": "Alice", "extra": "ignored"}) == "Hi Alice"

    def test_body_with_no_placeholders_renders_unchanged(self) -> None:
        assert render_template("Just a plain message", {}) == "Just a plain message"

    def test_invalid_placeholder_shape_raises(self) -> None:
        # ``string.Template`` raises ``ValueError`` for
        # malformed placeholders (e.g. a bare ``$`` followed
        # by punctuation that is not a valid identifier).
        # ``render_template`` wraps that into a
        # ``TemplateRenderError`` so callers only need to
        # handle one exception type.
        with pytest.raises(TemplateRenderError):
            render_template("Bad $! placeholder", {})

    def test_unicode_values_round_trip(self) -> None:
        assert render_template("Hallo $name", {"name": "Müller"}) == "Hallo Müller"
