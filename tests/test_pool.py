import pytest
from qoder2oapi.models import AccountRecord
from qoder2oapi.pool import AccountPool, pool
import qoder2oapi.token_store as ts_mod


@pytest.mark.asyncio
async def test_empty_pool_next_account_is_none():
    assert await pool.next_account() is None
    assert pool.peek_usable() == []


@pytest.mark.asyncio
async def test_three_accounts_round_robin_with_skip_quota():
    ts_mod.token_store.clear()
    acc_a = AccountRecord(
        id="acc-a",
        kind="oauth",
        access_token="token-a",
        user_id="user-a",
        machine_id="m-a",
    )
    acc_b = AccountRecord(
        id="acc-b",
        kind="oauth",
        access_token="token-b",
        user_id="user-b",
        machine_id="m-b",
        skip_quota=True,
    )
    acc_c = AccountRecord(
        id="acc-c",
        kind="oauth",
        access_token="token-c",
        user_id="user-c",
        machine_id="m-c",
    )
    ts_mod.token_store.save_all([acc_a, acc_b, acc_c])

    # Cursor starts at 0 -> acc_a, then acc_c, then acc_a
    first = await pool.next_account()
    assert first is not None and first.id == "acc-a"

    second = await pool.next_account()
    assert second is not None and second.id == "acc-c"

    third = await pool.next_account()
    assert third is not None and third.id == "acc-a"


def test_upsert_same_user_id_replaces():
    ts_mod.token_store.clear()
    acc1 = AccountRecord(
        id="id-1",
        kind="oauth",
        access_token="tok-1",
        user_id="user-same",
        name="Name1",
        machine_id="m1",
    )
    ts_mod.token_store.upsert(acc1)
    accounts = ts_mod.token_store.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].id == "id-1"
    assert accounts[0].name == "Name1"

    acc2 = AccountRecord(
        id="id-different",
        kind="oauth",
        access_token="tok-2",
        user_id="user-same",
        name="Name2",
        machine_id="m2",
    )
    res = ts_mod.token_store.upsert(acc2)
    assert res.id == "id-1"  # keeps id of original row
    accounts = ts_mod.token_store.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].id == "id-1"
    assert accounts[0].name == "Name2"
    assert accounts[0].access_token == "tok-2"


def _acc(**kwargs: object) -> AccountRecord:
    defaults: dict[str, object] = {
        "id": "id-1",
        "kind": "oauth",
        "access_token": "tok-1",
        "user_id": "user-1",
        "machine_id": "m-1",
    }
    defaults.update(kwargs)
    return AccountRecord.model_validate(defaults)


def test_accounts_json_round_trip():
    ts_mod.token_store.clear()
    ts_mod.token_store.upsert(
        _acc(pat="pt-secret", skip_quota=True, last_error="quota")
    )
    path = ts_mod.token_store.accounts_file
    assert path.name == "accounts.json"
    text = path.read_text(encoding="utf-8")
    assert "pt-secret" in text
    assert "tok-1" in text
    assert "quota_snapshot" not in text
    assert "last_error" not in text

    payload = ts_mod.token_store.export_payload()
    assert payload["version"] == 1
    assert payload["accounts"][0]["access_token"] == "tok-1"

    ts_mod.token_store.clear()
    assert ts_mod.token_store.list_accounts() == []
    ts_mod.token_store.import_accounts(payload, mode="replace")
    restored = ts_mod.token_store.list_accounts()
    assert len(restored) == 1
    assert restored[0].access_token == "tok-1"
    assert restored[0].pat == "pt-secret"
    assert restored[0].skip_quota is True
    assert restored[0].last_error == ""


def test_import_merge_updates_same_user_and_appends():
    ts_mod.token_store.clear()
    ts_mod.token_store.upsert(_acc(id="keep-id", user_id="user-a", access_token="old"))
    result = ts_mod.token_store.import_accounts(
        {
            "accounts": [
                {
                    "id": "other-id",
                    "kind": "oauth",
                    "access_token": "new-a",
                    "user_id": "user-a",
                    "machine_id": "m-a",
                },
                {
                    "id": "id-b",
                    "kind": "pat",
                    "access_token": "new-b",
                    "user_id": "user-b",
                    "machine_id": "m-b",
                    "pat": "pt-b",
                },
            ]
        },
        mode="merge",
    )
    assert result["updated"] == 1
    assert result["imported"] == 1
    assert result["skipped"] == 0
    accounts = ts_mod.token_store.list_accounts()
    assert len(accounts) == 2
    by_user = {acc.user_id: acc for acc in accounts}
    assert by_user["user-a"].id == "keep-id"
    assert by_user["user-a"].access_token == "new-a"
    assert by_user["user-b"].access_token == "new-b"


def test_import_replace_swaps_pool():
    ts_mod.token_store.clear()
    ts_mod.token_store.upsert(_acc(id="old", user_id="old-user"))
    result = ts_mod.token_store.import_accounts(
        [
            {
                "kind": "oauth",
                "access_token": "only",
                "user_id": "new-user",
                "machine_id": "m-new",
            }
        ],
        mode="replace",
    )
    assert result["imported"] == 1
    assert result["updated"] == 0
    accounts = ts_mod.token_store.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].user_id == "new-user"
    assert accounts[0].access_token == "only"


def test_import_skips_missing_or_masked_token():
    ts_mod.token_store.clear()
    result = ts_mod.token_store.import_accounts(
        {
            "accounts": [
                {"kind": "oauth", "user_id": "no-token"},
                {"kind": "oauth", "access_token": "***", "user_id": "masked"},
                {
                    "kind": "oauth",
                    "access_token": "real",
                    "user_id": "ok",
                    "machine_id": "m",
                },
            ]
        },
        mode="replace",
    )
    assert result["skipped"] == 2
    assert result["imported"] == 1
    assert len(ts_mod.token_store.list_accounts()) == 1


def test_clear_flags_is_idempotent():
    ts_mod.token_store.clear()
    ts_mod.token_store.upsert(
        _acc(skip_quota=True, skip_auth=True, last_error="boom")
    )
    updated = pool.update_flags("id-1", skip_quota=False, skip_auth=False)
    assert updated is not None
    assert updated.skip_quota is False
    assert updated.skip_auth is False
    assert updated.last_error == ""
    again = pool.update_flags("id-1", skip_quota=False, skip_auth=False)
    assert again is not None
    assert again.skip_quota is False
    assert again.skip_auth is False
