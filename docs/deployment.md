# Judging runtime

OrderFix runs as a mailbox client. Agentverse delivers messages; an operator must
still keep the Python process running. The mailbox is not Python hosting.

The current delivered runtime is local. A persistent hosted service has not been
verified. No paid service or recurring subscription has been created.

## Docker host

The included Compose configuration packages the synthetic demo for an existing
Docker host with persistent disk. It publishes no port and needs outbound HTTPS.
The configuration is provided for deployment; a container build has not yet been
verified in the current Windows environment.

1. Clone the repository onto the intended host.
2. Privately provision the **same** `AGENT_SEED` in its `.env`. Never commit it.
3. Register the identity once with `python -m orderfix.register --register` in the
   local setup environment. The runtime container does not receive the account API key.
4. Stop the old process before starting the host. Run only one active worker for
   this identity and SQLite database.
5. Run `docker compose up -d --build` and inspect `docker compose logs --tail=50`.
6. Verify the complete lookup, review, confirmation and status conversation in ASI:One.

The `orderfix_data` volume preserves the hosted database across container restarts.
It starts empty on a new host. Existing local conversations are **not** migrated
automatically: back up and copy the database while both workers are stopped if
they need to be preserved. Do not use `docker compose down -v` on data you need.

Keep the host available throughout judging. A sleeping free service or an ephemeral
filesystem is not suitable for retaining return requests. Test recovery after a
restart before presenting the deployment as reliable.

## Local fallback

Run `python -m orderfix.agent` from the configured local environment. Keep the
terminal, computer and network active during judging. Disclose this availability
limit; do not describe the laptop as a deployed always-on service.
