import os
import sys
import importlib.util
import types


def load_startup():
    # prevent top-level exit by mocking CF_TOKEN check: set dummy env
    os.environ["CF_TOKEN"] = "dummy"
    os.environ["CF_DOMAIN"] = "example.com"
    # isolate from any MODEL* env vars leaked into the test shell
    for k in list(os.environ):
        if k.upper() in ("MODEL", "MODEL_NAME", "MODEL_QUANT"):
            os.environ.pop(k, None)
    src = open("scripts/startup.py").read()
    exec_globals = {"__file__": "scripts/startup.py"}
    exec(compile(src.split("def main():")[0], "scripts/startup.py", "exec"), exec_globals)
    return exec_globals["get_secret"]


def test_get_secret_env():
    # isolate from any real Kaggle secret so we test pure env precedence
    fake = types.ModuleType("kaggle_secrets")

    class FakeClient:
        def get_secret(self, k):
            raise KeyError(k)

    fake.UserSecretsClient = FakeClient
    sys.modules["kaggle_secrets"] = fake
    try:
        get_secret = load_startup()
        os.environ["MODEL"] = "mistralai/devstral-small-2507"
        assert get_secret("MODEL") == "mistralai/devstral-small-2507"
        os.environ.pop("MODEL", None)
        os.environ["MODEL_NAME"] = "qwen/qwen3.5-35b-a3b"
        assert get_secret("MODEL_NAME") == "qwen/qwen3.5-35b-a3b"
        os.environ.pop("MODEL_NAME", None)
        assert get_secret("MODEL") is None
    finally:
        sys.modules.pop("kaggle_secrets", None)


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
        MODEL_DEFAULT = "lmstudio-community/Qwen3-Coder-30B-A3B-GGUF"
        MODEL = gs("MODEL") or gs("MODEL_NAME") or MODEL_DEFAULT
        if not MODEL or not MODEL.strip():
            MODEL = MODEL_DEFAULT
        return MODEL.strip()

    assert resolve(None, None) == "lmstudio-community/Qwen3-Coder-30B-A3B-GGUF"
    assert resolve("mistralai/devstral-small-2507", None) == "mistralai/devstral-small-2507"
    assert resolve("", "qwen/qwen3.5-35b-a3b") == "qwen/qwen3.5-35b-a3b"
    assert resolve("   ", None) == "lmstudio-community/Qwen3-Coder-30B-A3B-GGUF"


def test_startup_import_main():
    # ensure main exists and is callable without installing package
    spec = importlib.util.spec_from_file_location("startup_main", "scripts/startup.py")
    mod = importlib.util.module_from_spec(spec)
    src = open("scripts/startup.py").read()
    globs = {"__file__": "scripts/startup.py"}
    # exec whole file - top-level now only defines functions, main not auto-called
    exec(compile(src, "scripts/startup.py", "exec"), globs)
    assert "main" in globs
    assert callable(globs["main"])
