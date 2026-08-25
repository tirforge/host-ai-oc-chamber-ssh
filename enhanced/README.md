# Enhanced Tool Calling (combined)

Previously discussed repo that enhances tool calling for small/local models is now integrated:

## 1. `agustif/opencode-lmstudio` (already in opencode.json)
- Auto-discovers LM Studio models via `GET /v1/models`, no manual model list
- Already enabled: `plugin: ["opencode-lmstudio@1.0.0-rc.2"]` with `tool_call:true`

## 2. `mebassett/smallcode` (added as fallback harness) - forgiving parser for dual-T4 small-model use
- Multi-format parser: native OpenAI JSON, Hermes, XML, YAML, plain text + auto-repair (wrong param names, truncated JSON)
- Patch-first edits (search-and-replace, not full file rewrite) - critical for 7B-20B where full rewrites hallucinate
- TODO-file decomposition + governor verification
- 2-stage tool routing (halves schema overhead for 8-16k context)
- SmallCode is terminal-native, points at same LM Studio endpoint `http://localhost:1234/v1`

Clone + run fallback when opencode stalls on tiny context:
```bash
git clone https://github.com/mebassett/smallcode
cd smallcode
npm install
SMALLCODE_ENDPOINT=http://localhost:1234/v1 SMALLCODE_MODEL=qwen3-coder-30b-a3b node bin/smallcode.js --cwd ../your-project
```

## How host uses it
- Default: `opencode web` (port 2456) with `tool_call:true` - best for Qwen3-Coder 30B A3B (your dual-T4 default)
- Fallback: `smallcode` harness auto-enabled if `opencode` returns malformed tool JSON 3x - see `scripts/startup.py` fallback
- Both share same LM Studio + same 4-subdomain tunnel (ai/oc/chamber/ssh)

Repo refs:
- https://github.com/agustif/opencode-lmstudio
- https://github.com/mebassett/smallcode
- https://github.com/goniz/opencode-local-provider (alternative multi-target provider, not enabled by default)
