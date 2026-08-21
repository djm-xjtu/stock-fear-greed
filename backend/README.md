# FastAPI ByteCloud SSO template

The SSO middleware is enabled by default and protects **every HTTP handler**.
It requires a JWT in
`x-jwt-token`, forwards that token to ByteCloud's `/auth/api/v1/userinfo`
endpoint, and stores the verified profile in `request.state.current_user`.
Missing, invalid, expired, or unverifiable tokens return `403` with
`{"detail":"unauthorized: missing or invalid jwt token"}` before any route
handler runs.

The React SSO template loads the signed-in user's deployment profile from
`GET /api/agents/v2/deployment/user` and sends the JWT in the `x-jwt-token`
header. Deployed AIME app hosts route that request to the matching backend
host: `*.aime-app.tiktok-row.net` uses `aime.tiktok-row.net`; all other hosts
use `aime.bytedance.net`.

Set `JWT_SERVER` for the target environment. `JWT_USERINFO_URL` can override
the full token-validation URL when required.

## API path convention

All HTTP endpoints in the generated service start with `/api`. The included
example endpoints are `GET /api`, `GET /api/v1/ping`, and `GET`/`POST
/api/users`. The OpenAPI specification and interactive documentation are at
`/api/openapi.json`, `/api/docs`, and `/api/redoc`. Keep the `/api` prefix for
new business endpoints.

## Connecting a frontend

If the frontend also calls APIs from this generated backend, deploy this
backend and set its public domain in the frontend environment file:

```dotenv
VITE_API_PROXY_TARGET=https://api.example.com
```

The React development server proxies `/api/*` to that target. Configure the
production web server, gateway, or Ingress to proxy `/api/*` as well.
