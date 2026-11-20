"""My models: register models with their name and their own key."""

import pytest
from fastapi.testclient import TestClient
from test_providers import ANTHROPIC, GROQ, OPENAI, Fake, agent_spec, calls  # noqa: F401  (fixtures and helpers)
from test_providers import fake as fake  # noqa: F401

from haresu import credentials, engine, model_list, providers, registry
from haresu.api import create_app

GROQ2 = "gsk_" + "H" * 40


def test_registering_validates_and_does_not_repeat(fake):
    f, ws = fake
    credentials.add("groq", "G", GROQ)
    e = registry.add("groq", "llama-x", "Fast Llama")
    assert e["name"] == "Fast Llama" and e["provider"] == "groq" and e["model_id"] == "llama-x" and e["key_id"] is None
    assert registry.list_()[0]["value"] == "groq:llama-x"
    with pytest.raises(registry.RegistryError, match="is already registered"):
        registry.add("groq", "groq:llama-x")  # with or without the provider prefix it is the same model
    assert registry.add("groq", "other")["name"] == "other"  # without a name, the id is used
    for provider, model, msg in [("nope", "x", "does not exist"), ("groq", "", "model id"), ("groq", "two words", "without spaces")]:
        with pytest.raises(registry.RegistryError, match=msg):
            registry.add(provider, model)
    with pytest.raises(registry.RegistryError, match="does not exist or belongs to another provider"):
        registry.add("groq", "z", key_id="made-up")
    registry.update(e["id"], name="Swift Llama")
    assert registry.list_()[0]["name"] == "Swift Llama"
    registry.remove(e["id"])
    assert [m["model_id"] for m in registry.list_()] == ["other"]
    with pytest.raises(registry.RegistryError, match="does not exist"):
        registry.remove(e["id"])


def test_each_registered_model_uses_its_key(fake):
    f, ws = fake
    credentials.add("groq", "Personal", GROQ)
    trabajo = credentials.add("groq", "Work", GROQ2, activate=False)  # the provider's one is still “Personal”
    registry.add("groq", "with-own-key", "With key", key_id=trabajo)
    registry.add("groq", "without-own-key", "No key")
    f.script = [{"content": "a"}, {"content": "b"}, {"content": "c"}]
    engine.run(agent_spec("groq:with-own-key"), "hello")
    engine.run(agent_spec("groq:without-own-key"), "hello")
    engine.run(agent_spec("groq:not-registered"), "hello")
    assert [q["auth"] for q in f.requests] == [f"Bearer {GROQ2}", f"Bearer {GROQ}", f"Bearer {GROQ}"]
    # If that key is removed, the model goes back to the provider's key and the list reports it.
    credentials.remove(trabajo)
    assert registry.list_()[0]["key_missing"] is True
    f.requests.clear()
    f.script = [{"content": "d"}]
    engine.run(agent_spec("groq:with-own-key"), "hello")
    assert f.requests[0]["auth"] == f"Bearer {GROQ}"


def test_the_own_key_also_works_for_anthropic(fake, monkeypatch):
    import anthropic

    seen = []

    class C:
        def __init__(self, **kw): seen.append(kw.get("api_key"))
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw): raise anthropic.APIConnectionError(request=None)
    monkeypatch.setattr(anthropic, "Anthropic", C)
    credentials.add("anthropic", "Personal", ANTHROPIC)
    otra = credentials.add("anthropic", "Other", "sk-ant-api03-" + "Z" * 40, activate=False)
    registry.add("anthropic", "claude-own", "Own", key_id=otra)
    for model in ("anthropic:claude-own", "claude-other"):
        with pytest.raises(providers.ProviderError):
            providers.for_model(model).chat(model=model, system="", messages=[{"role": "user", "content": "hello"}], tools=[])
    assert seen == ["sk-ant-api03-" + "Z" * 40, ANTHROPIC]


def test_api_of_my_models(fake, tmp_path):
    f, ws = fake
    c = TestClient(create_app(ws.root, static_dir=tmp_path / "nothing"))
    c.post("/api/settings/keys", json={"provider": "openai", "name": "Mine", "key": OPENAI})
    assert c.get("/api/registry").json() == {"models": []}
    # It is registered with a new key typed right there: it is saved with the provider, without changing the one in use.
    r = c.post("/api/registry", json={"provider": "openai", "model": "gpt-fake-a", "name": "My favorite",
                                      "new_key": {"name": "For testing", "key": "sk-proj-" + "N" * 40}})
    assert r.status_code == 200
    m = r.json()["models"][0]
    assert m["name"] == "My favorite" and m["key_name"] == "For testing" and m["key_last4"] == "NNNN" and "N" * 40 not in r.text
    openai = next(p for p in c.get("/api/settings").json()["providers"] if p["id"] == "openai")
    assert len(openai["keys"]) == 2 and next(k for k in openai["keys"] if k["id"] == openai["active"])["name"] == "Mine"
    # The selector list brings the registered ones apart from the full list.
    prov = c.get("/api/models").json()["providers"][0]
    assert [x["id"] for x in prov["models"]] == ["gpt-fake-a", "gpt-fake-b"]
    assert prov["registered"] == [{"value": "openai:gpt-fake-a", "id": "gpt-fake-a", "name": "My favorite", "key_id": m["key_id"]}]
    # Each node's warning says which key that model uses.
    res = c.get("/api/resolve", params={"model": "openai:gpt-fake-a"}).json()
    assert res["key_name"] == "For testing" and res["last4"] == "NNNN" and res["registered_name"] == "My favorite"
    assert c.get("/api/resolve", params={"model": "openai:gpt-fake-b"}).json()["key_name"] == "Mine"
    # Change name and key, and the validations.
    assert c.put(f"/api/registry/{m['id']}", json={"name": "Other name", "key_id": None}).json()["models"][0]["key_name"] is None
    assert c.put(f"/api/registry/{m['id']}", json={"key_id": "made-up"}).status_code == 400
    assert c.post("/api/registry", json={"provider": "openai", "model": "gpt-fake-a"}).status_code == 400  # repeated
    assert c.post("/api/registry", json={"provider": "openai", "model": "x", "new_key": {"key": "sk-ant-api03-" + "A" * 40}}).status_code == 400
    assert c.delete(f"/api/registry/{m['id']}").json() == {"models": []}
    assert c.delete("/api/registry/nothing").status_code == 404


def test_a_repeated_model_does_not_leave_an_orphan_key(fake, tmp_path):
    f, ws = fake
    c = TestClient(create_app(ws.root, static_dir=tmp_path / "nothing"))
    c.post("/api/settings/keys", json={"provider": "groq", "name": "G", "key": GROQ})
    assert c.post("/api/registry", json={"provider": "groq", "model": "m1"}).status_code == 200
    r = c.post("/api/registry", json={"provider": "groq", "model": "m1", "new_key": {"name": "Orphan", "key": GROQ2}})
    assert r.status_code == 400 and "is already registered" in r.json()["detail"]
    groq = next(p for p in c.get("/api/settings").json()["providers"] if p["id"] == "groq")
    assert [k["name"] for k in groq["keys"]] == ["G"]
