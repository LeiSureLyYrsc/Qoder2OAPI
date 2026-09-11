import json
import os
from pathlib import Path
import uuid
from cryptography.fernet import Fernet
from qoder2oapi.config import data_dir_path
from qoder2oapi.models import AccountRecord, TokenRecord


class TokenStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or data_dir_path
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.key_file = self.data_dir / "master.key"
        self.accounts_file = self.data_dir / "accounts.bin"
        self._fernet = Fernet(self._get_or_create_key())

    def _get_or_create_key(self) -> bytes:
        if self.key_file.exists():
            return self.key_file.read_bytes().strip()
        key = Fernet.generate_key()
        self.key_file.write_bytes(key)
        return key

    def list_accounts(self) -> list[AccountRecord]:
        if not self.accounts_file.exists():
            return []
        try:
            encrypted = self.accounts_file.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            raw_list = json.loads(decrypted.decode("utf-8"))
            if not isinstance(raw_list, list):
                return []
            return [AccountRecord(**item) for item in raw_list]
        except Exception:
            return []

    def save_all(self, accounts: list[AccountRecord]) -> None:
        raw_list = [acc.model_dump() for acc in accounts]
        data = json.dumps(raw_list, ensure_ascii=False).encode("utf-8")
        encrypted = self._fernet.encrypt(data)
        self.accounts_file.write_bytes(encrypted)

    def get(self, account_id: str) -> AccountRecord | None:
        for acc in self.list_accounts():
            if acc.id == account_id:
                return acc
        return None

    def upsert(self, account: AccountRecord) -> AccountRecord:
        accounts = self.list_accounts()
        if not account.id:
            account.id = str(uuid.uuid4())

        found_idx = -1
        for idx, existing in enumerate(accounts):
            if existing.id == account.id:
                found_idx = idx
                break
            if account.user_id and existing.user_id == account.user_id:
                account.id = existing.id
                found_idx = idx
                break

        if found_idx >= 0:
            accounts[found_idx] = account
        else:
            accounts.append(account)

        self.save_all(accounts)
        return account

    def delete(self, account_id: str) -> bool:
        accounts = self.list_accounts()
        new_list = [acc for acc in accounts if acc.id != account_id]
        if len(new_list) == len(accounts):
            return False
        self.save_all(new_list)
        return True

    def load_token(self) -> AccountRecord | None:
        accounts = self.list_accounts()
        if not accounts:
            return None
        # return first usable or first account or None
        for acc in accounts:
            if acc.enabled and not acc.skip_quota and not acc.skip_auth and acc.access_token:
                return acc
        return accounts[0]

    def save_token(self, token: TokenRecord | AccountRecord) -> AccountRecord:
        if isinstance(token, AccountRecord):
            account = token
            if not account.id:
                account.id = str(uuid.uuid4())
            if not account.kind:
                account.kind = "oauth"
        else:
            dump = token.model_dump()
            account = AccountRecord(
                id=str(uuid.uuid4()),
                kind="oauth",
                **dump,
            )
        return self.upsert(account)

    def clear(self) -> None:
        if self.accounts_file.exists():
            os.remove(self.accounts_file)


token_store = TokenStore()
