from qoder2oapi.catalog import catalog_manager
from qoder2oapi.names import alias_to_internal_key, catalog_key_candidates, public_id_for_key, public_name_for_key


def test_cn_prefix_on_known_and_unknown_keys():
    assert public_id_for_key("dmodel") == "cn/deepseek-v4-pro"
    assert public_id_for_key("dfmodel") == "cn/deepseek-v4-flash"
    assert public_id_for_key("gmodel") == "cn/glm-5.3"
    assert public_id_for_key("gfmodel") == "cn/glm-5.3-flash"
    assert public_id_for_key("qmodel_38max") == "cn/qwen3.8-max"
    assert public_id_for_key("qfmodel") == "cn/qwen3.8-flash"
    assert public_id_for_key("mmodel") == "cn/minimax-m3"
    assert public_id_for_key("mystery") == "cn/mystery"


def test_prefix_and_flash_aliases_resolve():
    assert alias_to_internal_key("cn/deepseek-v4-pro") == "dmodel"
    assert alias_to_internal_key("deepseek-v4-pro") == "dmodel"
    assert alias_to_internal_key("cn/deepseek-v4-flash") == "dfmodel"
    assert alias_to_internal_key("deepseek-flash") == "dfmodel"
    assert alias_to_internal_key("DeepSeek-Flash") == "dfmodel"
    assert alias_to_internal_key("cn/deepseek-flash") == "dfmodel"
    assert alias_to_internal_key("cn/deepseek-v4.1-flash") == "dfmodel"
    assert alias_to_internal_key("qoder-cn/auto") == "auto"
    assert alias_to_internal_key("cn/glm-5.3-flash") == "gfmodel"
    assert alias_to_internal_key("gm53model") == "gmodel"
    assert alias_to_internal_key("qmodel_preview") == "qmodel_38max"


def test_display_names_match_client_labels():
    assert public_name_for_key("dfmodel") == "DeepSeek Flash"
    assert public_name_for_key("dmodel") == "DeepSeek V4 Pro"
    assert public_name_for_key("qfmodel") == "Qwen 3.8 Flash"
    assert public_name_for_key("mmodel") == "MiniMax M3"


def test_catalog_lookup_falls_back_to_sibling_keys():
    assert "qmodel_preview" in catalog_key_candidates("cn/qwen3.8-max")
    assert "gm53model" in catalog_key_candidates("cn/glm-5.3")
    catalog_manager.models_by_key = {
        "qmodel_preview": {"key": "qmodel_preview"},
        "dfmodel": {"key": "dfmodel"},
    }
    qwen = catalog_manager.get_model("cn/qwen3.8-max")
    flash = catalog_manager.get_model("deepseek-flash")
    flash_v41 = catalog_manager.get_model("cn/deepseek-v4.1-flash")
    assert qwen is not None and qwen["key"] == "qmodel_preview"
    assert flash is not None and flash["key"] == "dfmodel"
    assert flash_v41 is not None and flash_v41["key"] == "dfmodel"
