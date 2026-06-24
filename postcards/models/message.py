"""Typed message — wraps the free-text greeting for a postcard.

The Swiss Postcard Creator accepts up to 500 characters in the
``message`` field. :class:`Message` is a thin frozen-dataclass
wrapper that enforces the length cap at construction time so
caller code fails fast instead of getting a server-side rejection
on send.

The wrapper exists primarily for **type discrimination** — without
it, a function that takes a ``message: str`` cannot tell whether
the string is the postcard greeting, the salutation, the city
name, or any other free-text field. With :class:`Message`, the
type checker flags accidental swaps at the call site.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Maximum number of characters accepted by the Swiss Postcard Creator
#: for the ``message`` field. Past this length the API rejects the
#: card without further diagnostics; :class:`Message` rejects the
#: value at construction time so the CLI can fail earlier and louder.
MAX_MESSAGE_LENGTH: int = 500


@dataclass(frozen=True)
class Message:
    """The free-text greeting printed on the back of a postcard.

    The :class:`dataclasses.dataclass` is ``frozen`` so an existing
    :class:`Message` can never be mutated mid-flight (the pipeline
    treats it as immutable after construction). Use
    :meth:`from_text` when you want a single-line constructor.
    """

    text: str

    def __post_init__(self) -> None:
        if len(self.text) > MAX_MESSAGE_LENGTH:
            raise ValueError(
                f"message exceeds the {MAX_MESSAGE_LENGTH}-character limit "
                f"(got {len(self.text)} characters)"
            )

    @classmethod
    def from_text(cls, text: str) -> Message:
        """Construct a :class:`Message` from a plain string.

        Equivalent to ``Message(text=text)`` — provided so the
        call site reads naturally next to other ``from_*`` builders.
        """
        return cls(text=text)

    def is_empty(self) -> bool:
        """Return ``True`` if the message has no printable content."""
        return not self.text.strip()

    def __str__(self) -> str:
        return self.text

    def __len__(self) -> int:
        return len(self.text)


__all__ = ["MAX_MESSAGE_LENGTH", "Message"]
