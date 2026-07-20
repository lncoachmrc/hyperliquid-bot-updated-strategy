from pathlib import Path


def test_openai_model_is_configurable_from_environment():
    source = Path("trading_agent.py").read_text(encoding="utf-8")

    assert 'os.getenv("OPENAI_MODEL")' in source
    assert '"gpt-5.6-terra"' in source
    assert "model=OPENAI_MODEL" in source
    assert 'model="gpt-5.1"' not in source


def test_env_example_documents_openai_model():
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "OPENAI_MODEL=gpt-5.6-terra" in env_example
