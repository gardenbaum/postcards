"""Template variable substitution.

The message templates use :class:`string.Template` syntax —
``$name`` and ``${name}``. ``string.Template`` is part of the
stdlib, well-documented, and unambiguous about edge cases:

* ``$$`` renders as a literal ``$`` (the standard escape).
* ``${name}`` is the form to use when the name is followed by
  a character that would otherwise be part of it (e.g.
  ``${name}!``). Without braces ``$name!`` would render
  ``$name!`` verbatim because ``!`` is not a valid identifier
  character, but the user-friendly form ``${name}`` is what
  the docstring recommends.

Why strict missing-key semantics
--------------------------------

:func:`string.Template.substitute` raises :class:`KeyError` on a
referenced but unprovided variable, which is exactly the
behaviour we want for a postcard: it is far better for the CLI
to refuse to send a half-rendered ``"Hi ${name},"`` than to
silently post the literal placeholder. We wrap the
:class:`KeyError` into a dedicated :class:`TemplateRenderError`
so the CLI can give a precise error message including the
missing variable name.
"""

from __future__ import annotations

import re
import string
from collections.abc import Mapping


class TemplateRenderError(ValueError):
    """Raised when a template references a missing variable.

    The CLI converts this into a user-facing error with the
    name of the missing variable.
    """


#: Pattern used to detect ``${name}`` placeholders in a template
#: before handing the body to :class:`string.Template`. We use
#: it to validate that ``identifier`` chars are well-formed
#: ahead of the actual substitution call so an obviously bad
#: template (``${1bad}``) is rejected with a clear error
#: instead of letting :class:`string.Template` raise a less
#: helpful exception.
_VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def render_template(body: str, variables: Mapping[str, object]) -> str:
    """Render ``body`` by substituting ``variables``.

    Parameters
    ----------
    body:
        The template body, e.g. ``"Hi $name, greetings from Zurich"``.
    variables:
        Mapping of variable names to their values. Values are
        coerced to :class:`str` before substitution; ``None``
        becomes the string ``"None"`` (we intentionally do not
        silently drop missing values — see below).

    Raises
    ------
    TemplateRenderError
        When ``body`` references a variable not present in
        ``variables``, or when a ``${...}`` placeholder contains
        an invalid identifier.
    """
    # Defensive validation: scan the body for ``${name}`` blocks
    # with invalid identifiers so we can give a clean error
    # rather than letting ``string.Template`` raise
    # ``ValueError: Invalid placeholder in string``.
    for match in re.finditer(r"\$\{([^}]*)\}", body):
        identifier = match.group(1).strip()
        if not _VALID_NAME.match(identifier):
            raise TemplateRenderError(
                f"invalid placeholder '${{{identifier}}}' in template body "
                "(identifiers must start with a letter or underscore and "
                "contain only letters, digits and underscores)"
            )

    template = string.Template(body)

    # Build a string-valued copy of the variables so we never
    # smuggle a non-stringifiable value through. ``str(None)``
    # is ``'None'`` — but ``${name}``-style templates are
    # expected to receive strings; users who want empty values
    # should pass an explicit empty string.
    string_vars: dict[str, str] = {str(k): str(v) for k, v in variables.items()}

    try:
        return template.substitute(string_vars)
    except KeyError as exc:
        missing = exc.args[0]
        raise TemplateRenderError(
            f"template references undefined variable ${missing!s} "
            "(supply it via --var name=value or via the templates render command)"
        ) from exc
    except ValueError as exc:
        # ``string.Template`` raises ``ValueError`` for
        # ``Invalid placeholder in string`` (e.g. a bare ``$``
        # followed by an invalid character). Surface it as a
        # ``TemplateRenderError`` so callers only need to
        # handle one exception type.
        raise TemplateRenderError(f"failed to render template: {exc}") from exc


__all__ = ["TemplateRenderError", "render_template"]
