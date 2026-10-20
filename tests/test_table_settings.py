from paperbridge.app import Settings


def test_default_and_saved_settings_accept_the_table_switch():
    assert Settings().translate_tables is False
    assert Settings(translate_tables=True, provider="api").model_dump()["translate_tables"] is True
    assert Settings(translate_tables=False, provider="local").model_dump()["translate_tables"] is False
