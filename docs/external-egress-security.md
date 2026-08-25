# External egress and MoySklad image security

## Egress inventory

The backend's network-capable boundaries are:

- `app/clients/moysklad.py`: authenticated JSON requests and product images;
- `app/clients/bitrix_*.py` and the legacy order refresh in `app/web.py`:
  Bitrix/TicTacToy JSON endpoints;
- `app/clients/wildberries_orders.py`: authenticated Wildberries order reads;
- `app/services/product_images.py`: explicit TicTacToy page/image imports;
- `app/auth.py`: SMTP authentication messages;
- maintenance/import scripts under `scripts/`, which are not imported by the app.

The normal CI entry point, `scripts/run_backend_tests.py`, clears integration
credentials and blocks non-loopback DNS, socket connections, Requests/urllib
transports (at the socket boundary), and `curl`/`wget` subprocesses. Local test
servers remain available on loopback and Unix sockets.

## MoySklad trust boundary

Credentials may be attached only after parsing an HTTPS URL whose normalized
origin is one of the three origins observed in the configured production API:
`api.moysklad.ru`, `miniature-prod.moysklad.ru`, or
`tinyimage-prod.moysklad.ru`, all on port 443. Userinfo, trailing-dot hosts,
non-default ports, alternate schemes and lookalike domains are rejected. Image
downloads additionally resolve every address and reject the request when any
answer is non-global.

Requests carrying Authorization never follow redirects automatically. Image
redirects are bounded and each target is parsed, origin-checked and DNS-checked
again before the next authenticated request. Cross-origin and downgrade
redirects therefore receive no request and no token.

Downloads use connect/read timeouts, streaming, a two-megabyte declared and
actual byte limit, a narrow MIME allowlist, and JPEG/PNG/GIF/WebP signatures.
Responses are closed on success and error. Exceptions expose only stable error
categories and HTTP status, never the token, response body, query string or full
source URL.

DNS validation and the TLS connection are deliberately left to Requests rather
than implementing custom TLS. This avoids weakening hostname verification. The
remaining DNS time-of-check/time-of-use window is bounded by the exact official
origin, rejection of every non-global DNS answer, HTTPS certificate validation,
and disabled redirects.

## Configuration and rollback

MoySklad, the protected Bitrix catalog, Wildberries, SMTP and the legacy Bitrix
exchange client perform no network operation when their required credentials or
endpoint configuration are absent. Production secrets are unchanged.

Rollback is application-only: deploy the previous commit. No schema, migration
history or business data is changed by this hardening.
