# Quickstart (15 minutes, laptop)

1. `uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -e ".[synthid,dev]"`
   (CPU torch is fine; no model download is needed until step 5.)
2. Terminal A: `MARKWRIGHT_DB=markwright.sqlite .venv/bin/uvicorn markwright.api.app:app`
   Terminal B: `export MARKWRIGHT_TOKEN=operator-token` (dev tokens; v1 uses OIDC).
3. Register and configure:
   `markwright model add demo-llm --version 1.0 --tokenizer gpt2`
   `markwright config create --model demo-llm --version 1.0 --scheme synthid-text`
4. Calibrate on the bundled starter corpus and activate (the result is honest about
   being provisional — see data/null/README.md):
   `markwright calibrate run <config_id> --null-corpus gutenberg-en-v1`
   `markwright calibrate accept <run_id>`
   `markwright config activate <config_id>`
5. Generate marked text with a small open model (downloads ~300 MB):
   `markwright generate test --config <config_id> --model HuggingFaceTB/SmolLM2-135M-Instruct --prompt "Write 300 words about tide pools."`
   (No model handy? `./scripts/demo.sh` runs the whole loop with processor-driven
   marked tokens instead — no LM required.)
6. `markwright verify --text "<the generated text>"` → a band, a calibrated p-value
   with its interval, the FPR target, caveats.
7. Try `markwright verify --text "Short sentence."`: it returns `INSUFFICIENT
   EVIDENCE` with no p-value, because short input never produces one.
