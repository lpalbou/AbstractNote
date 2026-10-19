"""The scripted model of the hermetic gateway answers every AbstractNote schema the runtime asks for.

The runtime reaches an openai-compatible endpoint through AbstractCore's PROMPTED structured output
(no ``response_format``: the JSON Schema is written into the prompt), so the stub must recognise the
schema there. One request is the real one the hermetic gateway's reflect automation sent (recorded
2026-10-19, fixtures/stub_llm_reflect_request.json); the others are built with AbstractCore's own
prompt builder from the runtime's own schema-to-model conversion. Review 02 M1.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from abstractnote.process.schemas import SCHEMAS, validate

ROOT = Path(__file__).resolve().parents[1]


def _stub():
    spec = importlib.util.spec_from_file_location("stub_llm", ROOT / "scripts" / "stub_llm.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_the_recorded_reflect_request_is_answered_with_valid_reflections():
    stub = _stub()
    body = json.loads((ROOT / "tests" / "fixtures" / "stub_llm_reflect_request.json").read_text(encoding="utf-8"))
    assert "response_format" not in body  # what the runtime really sends: the schema is in the prompt
    assert stub.schema_for(body) == "Reflections"
    validate("Reflections", stub.ANSWERS["Reflections"])


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_every_schema_prompted_by_abstractcore_is_recognised(name):
    pytest.importorskip("abstractruntime")
    from abstractcore.structured.handler import StructuredOutputHandler
    from abstractruntime.integrations.abstractcore.effect_handlers import _pydantic_model_from_json_schema

    stub = _stub()
    model = _pydantic_model_from_json_schema(SCHEMAS[name], name="LLM_StructuredOutput")
    prompt = StructuredOutputHandler()._create_schema_prompt("File this note.", model)
    body = {"model": "note-stub", "messages": [{"role": "system", "content": "Output JSON only."},
                                               {"role": "user", "content": prompt}]}
    assert stub.schema_for(body) == name
    validate(name, stub.ANSWERS[name])


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_every_schema_in_response_format_is_recognised(name):
    stub = _stub()
    body = {"messages": [{"role": "user", "content": "hi"}],
            "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": SCHEMAS[name]}}}
    assert stub.schema_for(body) == name


def test_a_plain_chat_request_gets_no_schema():
    assert _stub().schema_for({"messages": [{"role": "user", "content": "Say hello."}]}) is None
