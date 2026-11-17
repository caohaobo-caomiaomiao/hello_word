# API Automation Suite

A production-style HTTP API automation suite built on **pytest** and
**requests**. It ships with a small demo REST API, so the whole suite runs
offline, deterministically and in a few seconds — no external service, no
credentials, no flaky network.

```console
$ cd api-automation
$ pip install -r requirements.txt
$ python -m pytest
============================= 49 passed in 4.7s ==============================
```

## Highlights

- **Runs anywhere out of the box** — the session fixture boots a stdlib-only
  demo API on an ephemeral port; every test starts from the same seed data.
- **Reusable client** (`api/client.py`) with bearer auth, one structured log
  line per request, and bounded exponential-backoff retries for transient
  failures (`429/5xx`, connection errors) on replay-safe methods. `POST` is
  never replayed automatically.
- **Fluent assertions** — `response.expect_status(201).expect_json().expect_json_path("id", 1)`.
- **Contract tests** — JSON Schema validation for every response shape,
  including a consistent error envelope.
- **Data-driven tests** — payloads live in `tests/data/users.json`.
- **49 tests** across health, auth/authz, CRUD, validation, pagination,
  contract and resilience scenarios, organised with `smoke`, `regression`,
  `negative` and `contract` markers.
- **CI ready** — GitHub Actions matrix over Python 3.10/3.11/3.12 with a JUnit
  report artifact.

## Quick start

Requires Python 3.10+.

```bash
cd api-automation
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest
```

Run a subset:

```bash
python -m pytest -m smoke                        # fast critical path
python -m pytest -m "regression and not negative"
python -m pytest tests/test_users_crud.py -k roundtrip -v
python -m pytest --junitxml=reports/junit.xml
python -m pytest --log-cli-level=DEBUG     # verbose client logs
```

The live log shows every request, status and duration, which makes a failing
test easy to localise:

```text
06:14:47 INFO    api.client | POST http://127.0.0.1:46275/auth/login -> 200 in 1.3 ms (attempt 1/3)
06:14:47 INFO    api.client | POST http://127.0.0.1:46275/users -> 201 in 1.4 ms (attempt 1/3)
06:14:47 INFO    api.client | GET http://127.0.0.1:46275/users/26 -> 200 in 1.0 ms (attempt 1/3)
```

## Running against a real API

Set `API_BASE_URL` (or pass `--base-url`) to point the suite at a deployed
service instead of the bundled demo server:

```bash
API_BASE_URL=https://staging.example.com \
API_USERNAME=admin API_PASSWORD=secret \
python -m pytest -m smoke
```

The tests are written against the demo API's contract (`/health`, `/auth/login`,
`/users` CRUD, pagination and the error envelope). To use a different service,
adapt the endpoint paths, payloads and schemas in `tests/` and
`api/schemas.py`; the client, fixtures, retry policy and reporting are
service-agnostic.

> The demo server resets its dataset before every test. When testing an
> external service the reset hook is skipped, so write/delete tests will mutate
> that service — use a dedicated test environment.

### Run the demo API manually

```bash
python -m api.mock_server --port 8000
# Demo API listening on http://127.0.0.1:8000
# Demo credentials: admin / admin123
```

## Configuration

| Variable | CLI flag | Default | Purpose |
| --- | --- | --- | --- |
| `API_BASE_URL` | `--base-url` | bundled demo server | Target base URL |
| `API_USERNAME` | `--api-username` | `admin` | Login username |
| `API_PASSWORD` | `--api-password` | `admin123` | Login password |
| `API_TIMEOUT` | — | `10` | Per-request timeout (seconds) |
| `API_RETRIES` | — | `2` | Retries after the first attempt |
| `API_RETRY_BACKOFF` | — | `0.2` | Initial backoff (seconds, doubled per retry) |
| `API_VERIFY_SSL` | — | `true` | Verify TLS certificates |

## Layout

```text
api-automation/
├── api/
│   ├── client.py        # HTTP client: auth, logging, retries, fluent assertions
│   ├── config.py        # environment/CLI-driven settings
│   ├── schema.py        # readable JSON Schema assertions
│   ├── schemas.py       # response contracts for the demo API
│   └── mock_server.py   # dependency-free demo REST API
├── tests/
│   ├── test_health.py
│   ├── test_auth.py
│   ├── test_users_crud.py
│   ├── test_validation.py
│   ├── test_pagination.py
│   ├── test_contracts.py
│   ├── test_resilience.py
│   ├── test_data_driven.py
│   └── data/users.json
├── conftest.py          # settings, server lifecycle, clients, state reset
├── pytest.ini           # markers and live logging
└── requirements.txt
```

## How it works

1. **Session bootstrap** — `conftest.py` checks for an explicit base URL. If
   there is none it starts `api.mock_server` on `127.0.0.1:0` (ephemeral port)
   in a daemon thread and tears it down at session end.
2. **Deterministic state** — an autouse fixture calls `POST /admin/reset`
   before each test, restoring the 25 seeded users and the flaky-failure
   counters. Tests can run in any order.
3. **Clients** — `client` is unauthenticated; `auth_client` logs in once per
   test and attaches the bearer token. Both are closed by fixtures.
4. **Assertions** — status, content type, headers and JSON paths are asserted
   fluently; response bodies are validated against JSON Schema so a broken
   contract fails with the exact field path.
5. **Retries** — transient failures are retried only for replay-safe methods
   with exponential backoff and jitter, respecting `Retry-After`. Resilience
   tests simulate 503s through `/flaky`.

## Writing a new test

```python
from api.schema import validate_schema
from api.schemas import USER_SCHEMA

def test_create_user_with_defaults(auth_client):
    response = auth_client.post(
        "/users",
        json={"name": "New User", "email": "new.user@example.com"},
        expected_status=201,
    ).expect_json()
    validate_schema(response.json(), USER_SCHEMA)
    assert response.json()["role"] == "viewer"
```

Use `auth_client` for authenticated endpoints, `user_factory` to create unique
fixtures, and add any new response shape to `api/schemas.py`.

## CI

`.github/workflows/api-tests.yml` runs the suite on every push to `main` and
every pull request touching `api-automation/`, across Python 3.10, 3.11 and
3.12. Each job uploads `reports/junit.xml` as an artifact, so test results are
available even when the run fails.
