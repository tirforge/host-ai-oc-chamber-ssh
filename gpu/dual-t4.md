# Dual T4 Support (2x16GB)

Your host is dual T4 - this config is wired for it.

## Why dual T4 matters
- SM75 Turing, no BF16, FP8 broken on recent vLLM, 320GB/s per card
- Dense 27B Q4 ~17GB = ~15 tok/s max, even with MTP ~30 tok/s - can't hit 50-60
- **MoE 30B-A3B** (Qwen3-Coder 30B A3B) = 3.3B active -> 55-70 tok/s on same 2xT4 at Q4_K_M, still 50+ at Q6_K
- PCIe, no NVLink - splitting layers adds VRAM, not single-stream speed, but lets you fit Q4_K_M + 32k ctx comfortably

## Wiring
- `CUDA_VISIBLE_DEVICES=0,1` - LM Studio / llama.cpp auto-splits
- `lms get lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF -y` then `lms server start --port 1234 --cors --gpu max --jinja --spec-type draft-mtp`
- Keep `context_length 49152` (48K) - **64K OOMs on dual T4** (`unable to allocate CUDA0 buffer`); 262K needs >40GB so never use it
- `Q4_K_M` leaves ~14GB for KV headroom, `Q6_K` leaves ~10GB

## Verify on host
```bash
nvidia-smi  # should show 2x T4
lms ps      # shows split
curl http://localhost:1234/v1/models
curl http://localhost:2456/doc  # opencode
curl http://localhost:3000  # openchamber
cloudflared tunnel list
```

## Fallback for tool failures
If opencode stalls (small context, malformed JSON), host auto-falls back to `smallcode` harness (forgiving parser, patch edits) pointing at same `http://localhost:1234/v1` - see `enhanced/README.md`
