# Quickstart (15 minutes, laptop)
1. `pip install -e ".[synthid,dev]"` (CPU torch is fine for a 1–2 B model).
2. `uvicorn markwright.api.app:app` in one terminal; `export MARKWRIGHT_TOKEN=operator-token` in another.
3. `markwright init` → register `demo-llm 1.0` with tokenizer `google/gemma-2-2b-it`, pick `synthid-text`, keys are created in the local sealed vault.
4. `python hooks/hf_generate_example.py --config <id> --model google/gemma-2-2b-it --prompt "Write 300 words about tide pools."`
5. `markwright calibrate run <id> --null-corpus wiki-en` (bundled 2k-document sample) then `markwright config activate <id>`.
6. `markwright verify --text "<the generated text>"` → a band, a p-value with its interval, the FPR target, caveats.
7. Try `markwright verify --text "Short sentence."` → `INSUFFICIENT EVIDENCE`, no p-value. That is the point.
