# QuickFix setup

QuickFix extends OrderFix with cancellation and refund coordination for an
**existing paid pharmacy order**. It is not a medicine-shopping or new-checkout
implementation. The primary conversation selects an order, checks its policy,
reviews the amount, receives explicit approval, and reports the pharmacy,
payment/refund and notification outcomes independently.

## What is ready and what is pending

| Component | Current setup |
| --- | --- |
| Pharmacy peer | Persistent fictional-order simulator; connected teammate integration is unverified |
| Gateway | Local simulated payment/refund ledger; optional Razorpay test adapter needs private credentials and a real captured test payment |
| Email | Persistent English/Hindi/Kannada captures; no email account or real recipient is configured |
| Teammate mail agent | Being built; address and compatible responses are pending |
| ASI:One evidence | New QuickFix simulation verified live on 24 October 2026; shared conversation linked below |

The local peer service and protocol tests require no paid API, ASI1 key, pharmacy
account, email account or Razorpay credential. No money moves and captured email
does not reach an inbox. Mode labels do not constitute provider evidence.

## Install and test

Use Python 3.12 in the repository directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m orderfix.setup_local
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m orderfix.commerce.cli demo
```

`setup_local` preserves an existing `.env`; append the QuickFix example settings
manually if that file predates the extension. Preserve the existing private
`AGENT_SEED` to retain the registered OrderFix address. Do not replace it with a
teammate seed or a public example seed. Local peer identities are separate.

The bounded `commerce.cli demo` starts distinct QuickFix, pharmacy and mail
agents plus a synthetic ACP customer, performs the complete local workflow and
stops. It writes `evidence.json` (including a transcript) and persistent SQLite
records under the printed `data/quickfix-demo-...` directory. It does not register
those temporary agents or touch the existing public agent's seed. The business
flow uses SDK local dispatch; a separate HTTP loopback signature probe is
reported independently. This command needs no accounts or API keys.

`python -m orderfix.commerce.cli status` prints configuration readiness booleans
without secrets. It does not prove a peer is online or gateway credentials work.

## Configuration

### Laptop judging with local simulation peers

After the bounded demo passes, stop any older OrderFix process using the same
registered identity, then run this from the repository directory:

```powershell
.\.venv\Scripts\python.exe -m orderfix.commerce.judge
```

This keeps the existing private seed and registered mailbox identity, starts
local pharmacy/mail agents, and stores persistent simulation data separately in
`data/quickfix-judge`. Only the coordinator renews its existing public
registration; local peers are not published. The launcher does not edit `.env`.
It forces simulated payments, fictional pharmacy orders and captured mail even
if external credentials are configured. It disables optional LLM calls and uses
the original ACP handler with background result cards.

Open the existing OrderFix ASI:One chat and say **quickfix show my orders**.
Choose a card, review it, then approve. Keep this process, the laptop and its
network online. The English cancellation/refund simulation was verified live
through ASI:One. Do not describe its local peers as the connected teammate agents.

For a connected pharmacy/mail and Razorpay test setup, use `orderfix.agent`
with the configuration below instead of the simulation-only judging launcher.

### Connected integration configuration

These are the values in `.env.example`. They keep the extension disabled until
explicitly selected:

```dotenv
QUICKFIX_ENABLED=false
QUICKFIX_DB=data/quickfix.sqlite3
QUICKFIX_GATEWAY=simulated
QUICKFIX_MERCHANT_REF=demo-pharmacy
MEDGUARDIAN_AGENT_ADDRESS=
MAIL_AGENT_ADDRESS=
QUICKFIX_PHARMACY_MODE=simulated
QUICKFIX_MAIL_MODE=captured
QUICKFIX_AUTO_DEMO=false
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
```

- Set `QUICKFIX_ENABLED=true` to opt in to the extension on the existing agent.
- Supply the actual signed addresses of the running pharmacy and mail peers.
  The local simulator implements [our contract](quickfix-contract.md); the
  teammate agents are not assumed to implement it yet.
- `QUICKFIX_AUTO_DEMO=true` is only for a clearly labelled synthetic demo. It
  must not create arbitrary real patients or imply verified purchase ownership.
- Keep the database between runs: it contains bindings, cases, pending commands
  and duplicate-protection records. Use separate paths for independent demos.
- `QUICKFIX_GATEWAY=razorpay_test` requires the supported test-key prefix, matching
  merchant configuration and a provider-verified captured test payment. Keep
  secrets in local configuration. See [gateway setup](quickfix-gateway.md).
- Set `QUICKFIX_PHARMACY_MODE=connected` or `QUICKFIX_MAIL_MODE=connected` only
  when the relevant teammate contract and verified response handling are ready.
  Changing a label cannot turn a simulator into an external provider.

Once peer routing is configured, start the existing mailbox agent:

```powershell
.\.venv\Scripts\python.exe -m orderfix.agent
```

This startup continues to use the existing OrderFix identity. Keep its process,
network and persistent database available during judging. Enabling QuickFix does
not deploy or start a teammate's agent.

## Teammate handoff

QuickFix owns this cancellation/refund conversation and its commerce cases.
Med Guardian owns pharmacy decisions and medication context; the mail agent owns
delivery attempts and provider status. Pass authenticated opaque customer references
through typed delegation; forwarding all shoppers as free-form ACP text from one
shared agent address would mix their state in the reviewed upstream version.

The current Med Guardian repository needs an adapter before it can answer this
contract. Its published agent address is a reference, not successful integration
evidence. See the [pinned code review](medguardian-review.md) for the exact files
and required changes. Do not modify medication supply at payment approval;
receipt/delivery is a later, separately verified event.

The mail teammate needs `mail.send` and `mail.status`, signed sender validation,
durable event/recipient deduplication, and distinct captured, submitted and
delivered evidence. No real mail should be attempted until its account,
recipients and authorization are configured.

## Conversation and evidence

Use `show my orders`, `मेरे ऑर्डर दिखाओ`, or `ನನ್ನ ಆರ್ಡರ್ ತೋರಿಸಿ` after the
customer is bound. Follow the order card, inspect the review, approve the exact
action, then use `check status`. See the [walkthrough](quickfix-demo.md).

Enter `quickfix` in the existing OrderFix chat to select this extension. Once
selected, it remembers that conversation and its language. Results are pushed
back to the same ACP session as peer operations finish; repeated polling is not
required. Outside a QuickFix conversation, existing OrderFix requests continue
to use the original workflow.

For connected test customers, an authorized operator runs
`python -m orderfix.commerce.cli invite CUSTOMER_REF` after verifying the account
mapping with Med Guardian. The customer redeems that private one-use setup code
as `connect CODE`. The code expires after two hours and is not an order ID.
Never publish it. Automatic binding is available only in the all-simulation
mode. Keep separate databases for simulation and connected integrations;
existing case approvals reject changes of gateway account, merchant or peers.

An accepted synthetic order can demonstrate cancellation; a dispatched one must
be rejected by policy. Repeated confirmation and a restart/status check should
preserve the same outcome. Mail failure must not trigger another refund.

The [new shared ASI:One conversation](https://asi1.ai/shared-chat/56e03728-b610-4698-b18e-8f250451fb5b)
verifies the deployed English QuickFix simulation. Start with `quickfix show my
orders` through the existing public handle. Keep the judging process running,
the laptop awake and its network connected. A new demo video remains pending;
the earlier MP4 demonstrates the original return-request project only.
