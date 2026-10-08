"""Selecting the chat model: name matching, validation, and the agent following it.

The VM is replaced by stubs of the three ollama calls selection depends on.
"""

import pytest

from app.models import ollama, selection

AVAILABLE = [
    "gemma4:26b-a4b-it-q4_K_M",
    "nomic-embed-text:latest",
    "qwen2.5:14b-instruct-q4_K_M",
    "qwen2.5vl:7b",
    "qwen3:8b",
]

CAPABILITIES = {
    "gemma4:26b-a4b-it-q4_K_M": {"completion", "tools", "vision"},
    "nomic-embed-text:latest": {"embedding"},
    "qwen2.5:14b-instruct-q4_K_M": {"completion", "tools"},
    "qwen2.5vl:7b": {"completion", "vision"},
    "qwen3:8b": {"completion", "tools", "thinking"},
}


@pytest.fixture(autouse=True)
def vm(monkeypatch):
    async def list_available():
        return list(AVAILABLE)

    async def capabilities(tag):
        return frozenset(CAPABILITIES.get(tag, ()))

    async def resident():
        return [ollama.ResidentModel("qwen3:8b", 8_000_000_000, "")]

    monkeypatch.setattr(ollama, "list_available", list_available)
    monkeypatch.setattr(ollama, "capabilities", capabilities)
    monkeypatch.setattr(ollama, "resident", resident)
    selection.reset()
    yield
    selection.reset()


class TestMatchTag:
    def test_exact_tag(self):
        assert selection.match_tag("qwen3:8b", AVAILABLE) == "qwen3:8b"

    def test_latest_may_be_omitted(self):
        assert (
            selection.match_tag("nomic-embed-text", AVAILABLE)
            == "nomic-embed-text:latest"
        )

    def test_unambiguous_prefix(self):
        assert selection.match_tag("gemma4", AVAILABLE) == "gemma4:26b-a4b-it-q4_K_M"

    def test_prefix_is_case_insensitive(self):
        assert selection.match_tag("Gemma4", AVAILABLE) == "gemma4:26b-a4b-it-q4_K_M"

    def test_ambiguous_prefix_matches_nothing(self):
        # qwen2.5:14b and qwen2.5vl:7b - picking one would be a guess.
        assert selection.match_tag("qwen2.5", AVAILABLE) is None

    def test_role_name_resolves_to_its_tag(self):
        assert selection.match_tag("fast", AVAILABLE) == "qwen3:8b"

    def test_unknown_name(self):
        assert selection.match_tag("llama9", AVAILABLE) is None


class TestSelect:
    def test_starts_on_the_default(self):
        assert selection.current_tag() == selection.default_tag() == "qwen3:8b"

    @pytest.mark.asyncio
    async def test_switches_the_current_model(self):
        assert await selection.select("gemma4") == "gemma4:26b-a4b-it-q4_K_M"
        assert selection.current_tag() == "gemma4:26b-a4b-it-q4_K_M"

    @pytest.mark.asyncio
    async def test_default_goes_back(self):
        await selection.select("gemma4")
        assert await selection.select("default") == "qwen3:8b"
        assert selection.current_tag() == "qwen3:8b"

    @pytest.mark.asyncio
    async def test_selecting_the_default_tag_follows_the_default_role(
        self, monkeypatch
    ):
        """Picking the default by name must not pin it: a later OLLAMA_MODEL
        change should still move the agent."""
        await selection.select("qwen3:8b")
        monkeypatch.setenv("OLLAMA_MODEL", "other:7b")
        assert selection.current_tag() == "other:7b"

    @pytest.mark.asyncio
    async def test_rejects_an_embedding_model(self):
        with pytest.raises(selection.ModelSelectionError, match="embedding"):
            await selection.select("nomic-embed-text")
        assert selection.current_tag() == "qwen3:8b"

    @pytest.mark.asyncio
    async def test_rejects_a_model_without_tools(self):
        with pytest.raises(selection.ModelSelectionError, match="tools"):
            await selection.select("qwen2.5vl:7b")

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_model(self):
        with pytest.raises(selection.ModelSelectionError, match="llama9"):
            await selection.select("llama9")

    @pytest.mark.asyncio
    async def test_unknown_capabilities_do_not_block(self, monkeypatch):
        """An Ollama too old to report capabilities must not lock every model."""

        async def capabilities(tag):
            return frozenset()

        monkeypatch.setattr(ollama, "capabilities", capabilities)
        assert await selection.select("qwen2.5vl:7b") == "qwen2.5vl:7b"

    @pytest.mark.asyncio
    async def test_unreachable_vm_is_an_error_not_a_blind_switch(self, monkeypatch):
        async def list_available():
            return []

        monkeypatch.setattr(ollama, "list_available", list_available)
        with pytest.raises(selection.ModelSelectionError, match="unreachable"):
            await selection.select("gemma4")


class TestCatalog:
    @pytest.mark.asyncio
    async def test_leaves_out_embedding_models(self):
        tags = [m.tag for m in await selection.catalog()]
        assert "nomic-embed-text:latest" not in tags
        assert "qwen3:8b" in tags

    @pytest.mark.asyncio
    async def test_marks_current_default_resident_and_tools(self):
        await selection.select("gemma4")
        models = {m.tag: m for m in await selection.catalog()}

        assert models["gemma4:26b-a4b-it-q4_K_M"].current
        assert not models["qwen3:8b"].current
        assert models["qwen3:8b"].default
        assert models["qwen3:8b"].resident
        assert models["qwen3:8b"].roles == ("fast",)
        assert not models["qwen2.5vl:7b"].tools


class TestTools:
    @pytest.mark.asyncio
    async def test_switch_then_check(self):
        from app.tools.builtin.models import current_model, switch_model

        reply = await switch_model.ainvoke({"model": "deep"})
        assert "qwen2.5:14b-instruct-q4_K_M" in reply
        assert "qwen2.5:14b-instruct-q4_K_M" in current_model.invoke({})

    @pytest.mark.asyncio
    async def test_a_failed_switch_is_reported_not_raised(self):
        from app.tools.builtin.models import switch_model

        reply = await switch_model.ainvoke({"model": "llama9"})
        assert reply.startswith("Error:")

    @pytest.mark.asyncio
    async def test_list_marks_the_current_model(self):
        from app.tools.builtin.models import list_models

        listing = await list_models.ainvoke({})
        assert "- qwen3:8b (role: fast; current; default; loaded)" in listing


@pytest.mark.asyncio
async def test_agent_follows_the_selection():
    """The graph is built once at startup, so the model must be resolved per
    call - otherwise a switch would need a restart to take effect."""
    from app.langgraph.agent import make_model_selector

    select_model = make_model_selector([])
    assert select_model({}, None).model == "qwen3:8b"
    await selection.select("gemma4")
    assert select_model({}, None).model == "gemma4:26b-a4b-it-q4_K_M"
