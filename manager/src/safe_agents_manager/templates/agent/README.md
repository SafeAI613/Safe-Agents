# __AGENT_NAME__

Lorem ipsum dolor sit amet.

## Run from the command line

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                            # fill in OPENROUTER_API_KEY
python -m agent.cli
```

Commands in the CLI: `help` (or `?`), `exit`.

## Run in Safe Agents Desktop

```bash
safe-agents-manager check .            # validate before publishing
safe-agents-manager package .          # zip + sha256 + catalog entry
```

See the platform's agent contract for how to publish a release.
