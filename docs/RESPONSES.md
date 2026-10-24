# Sending once and viewing DRC responses

Use **Dashboard → Send once & view response** to choose a profile. The workbook is validated in the background before any request is made. Review the endpoint, filename, worksheet, inventory type, record count and first ten payload records. **Cancel** sends nothing. **Send inventory** submits the entire prepared snapshot to the production DRC endpoint exactly once; changes made to Excel after preparation are not included in that snapshot.

This action can capture a response even when `confirmed` or `success_field` has not been configured. It does not enable automatic synchronization or change those settings. Automatic requests continue to require the configured and confirmed success rule. Manual requests never enter the automatic retry queue, including after rate limits, network failures, server errors or application restart. To explicitly resend, prepare a new request using the same button and review it again.

After sending, the response viewer opens with the HTTP status, elapsed milliseconds, endpoint, source file, inventory kind and JSON or plain-text response body. Unknown 2xx responses remain **Needs review**. A configured complete-success rule can validate an import; common partial/failure indicators still override that rule. HTTP error responses show their saved server error body. Network errors show no server response and an uncertain remote outcome.

Use **Sync history → View response**, enter the history job ID, and choose an attempt in the dropdown to review earlier responses. Existing blocked jobs show **No request sent—no server response**. Older requests made before this feature have no recorded body. A response viewer cannot recover a response that was never received or saved.

Response records are stored in local SQLite alongside history. Request headers and API keys are not stored. The current API key and recognized authorization/API-key/token/password fields are redacted before a body is saved or shown. JSON is formatted after redaction, and all content is rendered as plain text. Stored bodies are capped at 256 KB; oversized bodies are omitted entirely and explicitly marked truncated, avoiding exposure of partially captured secrets. Response metadata is retained per request attempt. Local response records may contain inventory or other business details returned by DRC.

The database migration adds a manual-request flag to existing jobs and a separate responses table; profiles, credential storage and existing history are preserved. Interrupted requests require review and never automatically resume as manual submissions.

All development and verification requests use mocked HTTP. Installing or building this feature does not send an inventory test request. A live send requires your explicit **Send inventory** action inside the review dialog.

## Offline local setup diagnostics

Use Excel Mapping / API connection → Check local setup (no upload) for the selected inventory kind. It checks Windows Credential Manager and constructs/closes the real HTTPS client without contacting DRC. The packaged app also supports:

```powershell
.\DRCInventorySync.exe --diagnose --inventory-kind Natural --diagnostics-output C:\path\to\diagnostics.json
```

The report contains only availability flags, failure stage, exception type and function/line locations. It never includes credential values, raw exception messages, inventory, request headers or a live request. This mode does not start monitoring or open the application database.

Manual failures now distinguish credential lookup, client setup, request preparation/dispatch, response reading/processing and history saving. Failures before dispatch report No request sent. Errors after dispatch remain Needs review with an uncertain remote outcome, and manual retries stay disabled. If history cannot be saved, a sanitized in-memory response remains visible with an explicit warning that it is available in that window only.

The managed build runtime's SSL module requires codex-rejected-cas.pem beside its frozen module. The build spec includes that file when the Python runtime supplies it, retaining the runtime's certificate policy and full HTTPS verification. An offline packaged diagnostic originally reproduced FileNotFoundError in SSLContext creation before any HTTP dispatch.
