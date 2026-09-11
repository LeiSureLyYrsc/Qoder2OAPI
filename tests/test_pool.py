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
