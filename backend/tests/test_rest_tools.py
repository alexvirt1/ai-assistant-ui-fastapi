"""Declarative REST tool construction and template interpolation."""

from app.tools.rest.generic import _build_tool, _render


def test_render_substitutes_arguments_into_strings():
    assert _render("{city}", {"city": "Berlin"}) == "Berlin"
    assert _render("lat={lat}", {"lat": 52.52}) == "lat=52.52"


def test_render_walks_dicts_and_lists():
    template = {"query": {"q": "{city}"}, "tags": ["{city}", "fixed"]}
    assert _render(template, {"city": "Berlin"}) == {
        "query": {"q": "Berlin"},
        "tags": ["Berlin", "fixed"],
    }


def test_render_resolves_env_vars(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "s3cret")
    assert _render("${MY_TOKEN}", {}) == "s3cret"


def test_render_blanks_unset_env_vars(monkeypatch):
    monkeypatch.delenv("ABSENT_TOKEN", raising=False)
    assert _render("${ABSENT_TOKEN}", {}) == ""


def test_non_string_values_are_not_interpolated():
    """Regression: YAML numbers pass through untouched.

    A config that wrote `latitude: 43.65` instead of `latitude: "{latitude}"`
    still declared a latitude argument, so the tool looked correct and the model
    supplied coordinates — which were silently discarded, returning the same
    location for every query. Placeholders must be quoted strings.
    """
    rendered = _render({"latitude": 43.65107}, {"latitude": 52.52})
    assert rendered == {"latitude": 43.65107}


def test_build_tool_generates_arg_schema():
    tool, _ = _build_tool(
        {
            "name": "get_thing",
            "description": "Get a thing.",
            "url": "https://example.com/thing",
            "args": {
                "city": {"type": "string", "description": "City name."},
                "count": {"type": "integer", "description": "How many."},
            },
        }
    )
    assert tool.name == "get_thing"
    fields = tool.args_schema.model_fields
    assert set(fields) == {"city", "count"}
    assert fields["city"].annotation is str
    assert fields["count"].annotation is int


def test_declared_args_are_all_required():
    """Every arg becomes a required field, which is why configs should declare
    as few as possible for a small local model to get right."""
    tool, _ = _build_tool(
        {
            "name": "t",
            "description": "d",
            "url": "https://example.com",
            "args": {"a": {"type": "string"}, "b": {"type": "string"}},
        }
    )
    assert all(f.is_required() for f in tool.args_schema.model_fields.values())


def test_required_env_is_derived_from_template_references():
    _, required_env = _build_tool(
        {
            "name": "t",
            "description": "d",
            "url": "https://example.com",
            "headers": {"X-Key": "${SOME_KEY}", "X-Other": "${OTHER_KEY}"},
        }
    )
    assert required_env == ("OTHER_KEY", "SOME_KEY")


def test_tool_without_env_references_needs_none():
    _, required_env = _build_tool(
        {"name": "t", "description": "d", "url": "https://example.com"}
    )
    assert required_env == ()
