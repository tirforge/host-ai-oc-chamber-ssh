import json
import yaml


def test_cloudflared_wiring():
    cfg = yaml.safe_load(open("cloudflared/config.yml"))
    assert cfg["tunnel"] == "t4host"
    ingress = cfg["ingress"]
    assert ingress[0]["hostname"] == "ai.yourdomain.com"
    assert ingress[0]["service"] == "http://localhost:1234"
    assert ingress[1]["hostname"] == "oc.yourdomain.com"
    assert ingress[1]["service"] == "http://localhost:2456"
    assert ingress[2]["hostname"] == "chamber.yourdomain.com"
    assert ingress[2]["service"] == "http://localhost:3000"
    assert ingress[3]["hostname"] == "ssh.yourdomain.com"
    assert ingress[3]["service"] == "ssh://localhost:22"
    assert ingress[4]["service"] == "http_status:404"


def test_opencode_wiring():
    j = json.load(open("opencode.json"))
    assert "plugin" in j
    assert "opencode-lmstudio@1.0.0-rc.2" in j["plugin"]
    for prov in ["lmstudio-local", "lmstudio-tunneled"]:
        assert prov in j["provider"]
        m = j["provider"][prov]["models"]["qwen3-coder-30b-a3b"]
        assert m["tool_call"] is True
        assert m["reasoning"] is True
        assert m["limit"]["context"] == 32768


def test_dual_t4_config():
    j = json.load(open("gpu/dual-t4.json"))
    assert j["opencode"]["port"] == 2456
    assert j["cloudflared"]["subdomains"] == ["ai", "oc", "chamber", "ssh"]
    assert "Q4_K_M" in j["quantization"]


def test_host_sh_model_var():
    txt = open("scripts/host.sh").read()
    assert 'MODEL=${MODEL:-qwen/qwen3-coder-30b-a3b}' in txt
    assert 'opencode web --port 2456' in txt
    assert 'lms get "$MODEL"' in txt


def test_pyproject_entry():
    txt = open("pyproject.toml").read()
    assert 'host-stack = "scripts.startup:main"' in txt
    assert 'host-ai-oc = "scripts.startup:main"' in txt
