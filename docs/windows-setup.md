# Windows setup and testing

Use PowerShell and Python 3.12. The commands invoke the virtual environment's Python directly, so activating it or changing PowerShell's execution policy is unnecessary.

## Open the project

If you already have the delivered project folder, open PowerShell in that folder. To obtain published source from GitHub, the equivalent clone commands are:

```powershell
git clone https://github.com/Deepak-18-hub/orderfix-ai.git
Set-Location orderfix-ai
```

Check the deployment status in the README for source publication and live integration verification.

## Create the environment

For a normal Python 3.12 installation:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m orderfix.setup_local
```

The setup command creates `.env` from `.env.example` with a private random agent seed. If `.env` already exists, it is preserved. Keep `STORE_MODE=demo`; neither Shopify credentials nor an ASI1 API key is needed for the default demo.

### Alternative on the delivered Codex machine

The standard `py` launcher may be present without a registered Python 3.12 installation. The delivered workspace already has a prepared environment. From the project folder, use it directly:

```powershell
$orderfixPython = 'C:\Users\dpkig\Documents\Codex\2026-10-23\challenge-statement-asi-one-agent-challenge-2\work\.venv\Scripts\python.exe'
& $orderfixPython -m orderfix.setup_local
& $orderfixPython -m pytest
& $orderfixPython -m orderfix.cli --scripted --language en
& $orderfixPython -m orderfix.agent
```

This prepared environment uses the bundled base interpreter at `C:\Users\dpkig\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`. It avoids rerunning `ensurepip`, which was restricted by the automation sandbox's temporary-directory access during development. For the remaining commands on this page, substitute `& $orderfixPython` for `.\.venv\Scripts\python.exe` when using this environment.

This fallback is specific to the delivered machine; another contributor should use their own Python 3.12 installation. It does not require changing global `PATH` or writing files into the Python installation directory.

## Verify all three languages

For UTF-8 terminal output in the current PowerShell session:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
```

Run the tests and the three scripted demonstrations:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m orderfix.cli --scripted --language en
.\.venv\Scripts\python.exe -m orderfix.cli --scripted --language hi
.\.venv\Scripts\python.exe -m orderfix.cli --scripted --language kn
```

The scripts print a final `VERIFIED` line after order lookup, eligibility, confirmed submission, saved-return readback, duplicate-message replay and status retrieval. They also demonstrate an expired return window. They use synthetic local data and automatically confirm the review only in demo mode.

The recorded verification result is 159 passing automated tests plus successful English, Hindi and Kannada scripts. Your new execution is the current result; a stale transcript is not a substitute for running them.

## Try a manual conversation

```powershell
.\.venv\Scripts\python.exe -m orderfix.cli --language en --session my-demo
```

Enter:

```text
order #1001
return #1001 item 1 quantity 1 damaged
confirm TOKEN
return status #1001
```

Use the actual review token shown by OrderFix. `cancel TOKEN` cancels an unsubmitted review. `review` displays it again. `English`, `हिंदी` and `ಕನ್ನಡ` switch the reply language. Type `quit` to exit.

Reuse `--session my-demo` to resume that conversation and its saved records. Choose another session name for a fresh independent set of synthetic orders. Do not reset or delete the database merely to repeat a demo.

By default the CLI uses `data/local-demo.sqlite3`, while the ACP agent uses `ORDERFIX_DB` from `.env`, normally `data/orderfix.sqlite3`. Use `--db` when you intentionally want a particular CLI database.

## Start the ASI:One-facing agent

Create an Agentverse account API key and save it privately in `.env` as `AGENTVERSE_API_KEY`. Use the account that should own OrderFix. This is a registration credential and is separate from `ASI1_API_KEY`; never paste either key into chat. Keep the existing agent seed so the registration and running process use the same identity.

```powershell
.\.venv\Scripts\python.exe -m orderfix.register --dry-run
.\.venv\Scripts\python.exe -m orderfix.register --register
.\.venv\Scripts\python.exe -m orderfix.agent
```

The dry run previews public registration settings and whether a key is configured; it does not print secrets or register the agent. Running the helper with no flag also performs a dry run.

Use the registration helper once instead of the legacy Inspector connection route. Check that registration succeeded for the intended account, then keep the agent process running. Test it from the agent's Testing tab in Agentverse before testing ASI:One discovery. Verify the public profile, run the entire conversation inside ASI:One, and save its shared chat URL.

Mailbox delivery does not require a public endpoint or a temporary tunnel: incoming requests are queued by Agentverse and retrieved by the running agent. This behavior is described in the [current official SDK overview](https://docs.agentverse.ai/documentation/launch-agents/agentverse-sdk/overview). It does not make the laptop an always-on host.

Card metadata is enabled by default. If the client does not render a review card, use the exact text confirmation displayed in the reply. A local terminal demonstration does not verify the remote card renderer.

An always-on judging deployment is a separate, currently unverified step. The host must keep the process alive, retain the SQLite database across restarts and receive the private environment settings. No hosting account or paid plan is required to run the local tests, and a public tunnel is unnecessary for the mailbox configuration.

## Optional ASI1 configuration

Set `ASI1_API_KEY` privately in `.env` only after obtaining a key. Leave it empty to use the tested offline flows. Do not paste the key into a conversation, commit it or include it in a recording. With a key configured, unfamiliar messages may be sent to ASI1 for intent classification; explicit identifiers, policy checks and user confirmation still control the action.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| `py -3.12` cannot find Python | Use a normal Python 3.12 installation or the delivered machine's prepared environment above |
| Import error such as missing `uagents` | Run the requirements install using the same `.venv\Scripts\python.exe` used to start the app |
| Hindi or Kannada output is garbled | Set the current terminal's UTF-8 options above and use a terminal/font supporting the script |
| A new session cannot see an earlier return | Reuse the same sender/session and database; demo customers are deliberately isolated by conversation |
| A return window is expired after several days | The original fulfillment timestamp is persistent; create a fresh demo session for another sample customer |
| `AGENT_SEED` is missing or too short | Run `orderfix.setup_local`; if `.env` already exists, correct it privately rather than printing it |
| Mailbox registration reports a missing account key | Save the Agentverse account key privately as `AGENTVERSE_API_KEY`, then run `orderfix.register --register` |
| An older local agent reports that port 8001 is occupied | Stop the older instance; the mailbox-only build disables the legacy Inspector and does not need a public local server |
| A return may have succeeded but no success was verified | Send `status`; keep the original conversation and database so the journal can reconcile it |
| Agentverse shows an agent but ASI:One cannot complete a request | Verify mailbox connection, process availability and the entire ACP conversation; registration alone is insufficient |
| Async tests stall only in an automation sandbox | Windows event-loop startup uses a local socket pair; rerun in an ordinary permitted terminal or grant the automation the required local networking permission |

For the sandbox case, use a permitted terminal with a working local event loop. The mailbox configuration does not require exposing the local agent port publicly.
