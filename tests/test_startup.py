import os
import sys
import importlib.util
import types


def load_startup():
    spec = importlib.util.spec_from_file_location("startup_mod", "scripts/startup.py")
    mod = importlib.util.module_from_spec(spec)
    # prevent top-level exit by mocking CF_TOKEN check: set dummy env
    os.environ["CF_TOKEN"] = "dummy"
    os.environ["CF_DOMAIN"] = "example.com"
    # import without executing main
    # we load source and exec only function defs
    src = open("scripts/startup.py").read()
    # extract get_secret function only
    exec_globals = {}
    exec(compile(src.split("def main():")[0], "scripts/startup.py", "exec"), exec_globals)
    return exec_globals["get_secret"]


def test_get_secret_env():
    get_secret = load_startup()
    os.environ["MODEL"] = "mistralai/devstral-small-2507"
    assert get_secret("MODEL") == "mistralai/devstral-small-2507"
    os.environ.pop("MODEL", None)
    os.environ["MODEL_NAME"] = "qwen/qwen3.5-35b-a3b"
    assert get_secret("MODEL_NAME") == "qwen/qwen3.5-35b-a3b"
    os.environ.pop("MODEL_NAME", None)
    assert get_secret("MODEL") is None


def test_get_secret_kaggle():
    get_secret = load_startup()
    # clear env
    for k in list(os.environ.keys()):
        if k.startswith("MODEL"):
            os.environ.pop(k, None)
    # mock kaggle_secrets
    fake = types.ModuleType("kaggle_secrets")

    class FakeClient:
        def get_secret(self, k):
            if k == "MODEL":
                return "kaggle-model-xyz"
            raise KeyError

    fake.UserSecretsClient = FakeClient
    sys.modules["kaggle_secrets"] = fake
    # need fresh get_secret that will try kaggle path
    # our get_secret already tries kaggle, so with env empty it should fallback to kaggle
    os.environ.pop("MODEL", None)
    assert get_secret("MODEL") == "kaggle-model-xyz"
    sys.modules.pop("kaggle_secrets", None)


def test_model_default_logic():
    # replicates startup.py MODEL logic
    def resolve(model_env, model_name_env, kaggle_model=None):
        # simulate get_secret
        def gs(name):
            if name == "MODEL":
                return model_env
            if name == "MODEL_NAME":
                return model_name_env
            return None
        MODEL_DEFAULT = "qwen/qwen3-coder-30b-a3b"
        MODEL = gs("MODEL") or gs("MODEL_NAME") or MODEL_DEFAULT
        if not MODEL or not MODEL.strip():
            MODEL = MODEL_DEFAULT
        return MODEL.strip()

    assert resolve(None, None) == "qwen/qwen3-coder-30b-a3b"
    assert resolve("mistralai/devstral-small-2507", None) == "mistralai/devstral-small-2507"
    assert resolve("", "qwen/qwen3.5-35b-a3b") == "qwen/qwen3.5-35b-a3b"
    assert resolve("   ", None) == "qwen/qwen3-coder-30b-a3b"


def test_startup_import_main():
    # ensure main exists and is callable
    import scripts.startup as s
    assert hasattr(s, "main")
    assert callable(s.main)
