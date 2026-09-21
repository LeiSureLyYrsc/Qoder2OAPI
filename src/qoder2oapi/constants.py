GATEWAY = "https://gateway.qoder.com.cn"
OPENAPI = "https://openapi.qoder.com.cn"
LOGIN = "https://qoder.com.cn/device/selectAccounts"
CHAT_SIG_PATH = "/api/v2/service/pro/sse/agent_chat_generation"
CHAT_URL = f"{GATEWAY}/algo{CHAT_SIG_PATH}?FetchKeys=llm_model_result&AgentId=agent_common&Encode=1"
MODEL_LIST_URL = f"{GATEWAY}/api/v2/model/list"
MODEL_LIST_ALGO_URL = f"{GATEWAY}/algo/api/v2/model/list"
QUOTA_URL = f"{OPENAPI}/api/v2/quota/usage"
USERINFO_URL = f"{OPENAPI}/api/v1/userinfo"
PLAN_URL = f"{OPENAPI}/api/v2/user/plan"
DEVICE_POLL = f"{OPENAPI}/api/v1/deviceToken/poll"
JOB_TOKEN_EXCHANGE = f"{OPENAPI}/api/v1/jobToken/exchange"
JOB_TOKEN_REFRESH = f"{OPENAPI}/api/v1/jobToken/refresh"
REFRESH = f"{GATEWAY}/algo/api/v3/user/refresh_token"

RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDA8iMH5c02LilrsERw9t6Pv5Nc
4k6Pz1EaDicBMpdpxKduSZu5OANqUq8er4GM95omAGIOPOh+Nx0spthYA2BqGz+l
6HRkPJ7S236FZz73In/KVuLnwI8JJ2CbuJap8kvheCCZpmAWpb/cPx/3Vr/J6I17
XcW+ML9FoCI6AOvOzwIDAQAB
-----END PUBLIC KEY-----"""

COSY_VERSION = "1.0.0"
IDE_VERSION = ""
CLIENT_TYPE = "5"
DATA_POLICY = "disagree"
LOGIN_VERSION = "v2"
MACHINE_OS = "x86_64_windows"
MACHINE_TYPE = "5"

STD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
CUSTOM_ALPHABET = "_doRTgHZBKcGVjlvpC,@aFSx#DPuNJme&i*MzLOEn)sUrthbf%Y^w.(kIQyXqWA!"
